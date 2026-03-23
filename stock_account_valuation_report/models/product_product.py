# Copyright 2020 ForgeFlow S.L.
# Copyright 2019 Aleph Objects, Inc.
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).


from odoo import api, fields, models
from odoo.tools import float_compare


class ProductProduct(models.Model):
    _inherit = "product.product"

    stock_value = fields.Monetary(
        "Inventory Value",
        compute="_compute_inventory_value",
        currency_field="cost_currency_id",
    )
    account_value = fields.Monetary(
        "Accounting Value",
        compute="_compute_inventory_value",
        currency_field="cost_currency_id",
    )
    qty_at_date = fields.Float(
        "Inventory Quantity",
        compute="_compute_inventory_value",
        digits="Product Unit of Measure",
    )
    account_qty_at_date = fields.Float(
        "Accounting Quantity",
        compute="_compute_inventory_value",
        digits="Product Unit of Measure",
    )
    valuation_discrepancy = fields.Monetary(
        compute="_compute_inventory_value",
        search="_search_valuation_discrepancy",
        currency_field="cost_currency_id",
    )
    qty_discrepancy = fields.Float(
        compute="_compute_inventory_value",
        search="_search_qty_discrepancy",
        digits="Product Unit of Measure",
    )

    @api.model
    def _search_qty_discrepancy(self, operator, value):
        products = self.with_context(active_test=False).search(
            [("is_storable", "=", True)],
        )
        products_with_discrepancy = products.filtered(
            lambda pp: float_compare(
                pp.qty_at_date,
                pp.account_qty_at_date,
                precision_rounding=pp.uom_id.rounding,
            )
        )
        return [("id", "in", products_with_discrepancy.ids)]

    @api.model
    def _search_valuation_discrepancy(self, operator, value):
        products = self.with_context(active_test=False).search(
            [("is_storable", "=", True)],
        )
        products_with_discrepancy = products.filtered(
            lambda pp: float_compare(
                pp.valuation_discrepancy,
                0.0,
                precision_rounding=pp.cost_currency_id.rounding,
            )
        )
        return [("id", "in", products_with_discrepancy.ids)]

    def _get_valuation_aml_ids(self, product, to_date):
        valuation_account_id = product.categ_id.property_stock_valuation_account_id.id
        if not valuation_account_id:
            return []
        # pylint: disable=E8103
        query = """
            SELECT array_agg(aml.id)
            FROM account_move_line AS aml
            WHERE aml.product_id = %s
            AND aml.parent_state = 'posted'
            AND aml.company_id = %s
            AND aml.account_id = %s
            {where_date_clause}
        """
        params = (product.id, self.env.company.id, valuation_account_id)
        where_date_clause = "AND aml.date <= %s" if to_date else ""
        query = query.format(where_date_clause=where_date_clause)
        if to_date:
            params = params + (to_date,)
        self.env.cr.execute(query, params=params)
        row = self.env.cr.fetchone()
        return list(row[0]) if row and row[0] else []

    def _compute_inventory_value(self):
        self.env["account.move.line"].check_access("read")
        to_date = self.env.context.get("at_date", False)

        # 1) ACCOUNTING VALUES — restrict to valuation accounts only
        valuation_account_ids = set()
        for product in self:
            if product.valuation == "real_time":
                acc_id = product.categ_id.property_stock_valuation_account_id.id
                if acc_id:
                    valuation_account_ids.add(acc_id)

        accounting_values = {}
        if valuation_account_ids:
            # pylint: disable=E8103
            query = """
                SELECT aml.product_id, aml.account_id,
                    sum(aml.balance),
                    sum(aml.quantity)
                FROM account_move_line AS aml
                WHERE aml.product_id IN %s
                AND aml.parent_state = 'posted'
                AND aml.company_id = %s
                AND aml.account_id IN %s
                {where_date_clause}
                GROUP BY aml.product_id, aml.account_id
            """
            params = (
                tuple(self.ids),
                self.env.company.id,
                tuple(valuation_account_ids),
            )
            where_date_clause = "AND aml.date <= %s" if to_date else ""
            query = query.format(where_date_clause=where_date_clause)
            if to_date:
                params = params + (to_date,)
            self.env.cr.execute(query, params=params)
            for row in self.env.cr.fetchall():
                accounting_values[(row[0], row[1])] = (row[2], row[3])

        # 2) INVENTORY VALUES
        move_domain = [
            ("product_id", "in", self.ids),
            ("company_id", "=", self.env.company.id),
            ("state", "=", "done"),
        ]
        if to_date:
            move_domain.append(("date", "<=", to_date))

        moves = self.env["stock.move"].search(move_domain)
        move_values = {}
        for move in moves:
            if move.product_id.id not in move_values:
                move_values[move.product_id.id] = {"qty": 0, "value": 0}
            move_values[move.product_id.id]["qty"] += move.remaining_qty
            move_values[move.product_id.id]["value"] += move.remaining_value

        for product in self:
            if product.valuation == "real_time":
                valuation_account_id = (
                    product.categ_id.property_stock_valuation_account_id.id
                )
                value, qty = accounting_values.get(
                    (product.id, valuation_account_id)
                ) or (0, 0)
                product.account_value = value
                product.account_qty_at_date = qty
            else:
                product.account_value = 0
                product.account_qty_at_date = 0
            move_data = move_values.get(product.id, {})
            product.qty_at_date = move_data.get("qty", 0)
            product.stock_value = move_data.get("value", 0)
            if product.valuation == "real_time":
                product.valuation_discrepancy = (
                    product.stock_value - product.account_value
                )
                product.qty_discrepancy = (
                    product.qty_at_date - product.account_qty_at_date
                )
            else:
                product.valuation_discrepancy = 0
                product.qty_discrepancy = 0

    def action_view_amls(self):
        self.ensure_one()
        to_date = self.env.context.get("at_date", False)
        aml_ids = self._get_valuation_aml_ids(self, to_date)
        list_view_ref = self.env.ref("account.view_move_line_tree")
        form_view_ref = self.env.ref("account.view_move_line_form")
        return {
            "name": self.env._("Accounting Valuation at date"),
            "type": "ir.actions.act_window",
            "view_type": "form",
            "view_mode": "list,form",
            "context": self.env.context,
            "res_model": "account.move.line",
            "domain": [("id", "in", aml_ids)],
            "views": [(list_view_ref.id, "list"), (form_view_ref.id, "form")],
        }

    def action_view_valuation_layers(self):
        to_date = self.env.context.get("at_date", False)
        move_domain = [
            ("product_id", "in", self.ids),
            ("company_id", "=", self.env.company.id),
            ("state", "=", "done"),
        ]
        if to_date:
            move_domain.append(("date", "<=", to_date))
        moves = self.env["stock.move"].search(move_domain)
        action = self.env["ir.actions.actions"]._for_xml_id(
            "stock_account.stock_valuation_layer_report_action"
        )
        action["domain"] = [("id", "in", moves.ids)]
        action["context"] = {}
        return action

    def action_view_valuation_moves(self):
        self.ensure_one()
        to_date = self.env.context.get("at_date", False)
        move_domain = [
            ("product_id", "=", self.id),
            ("company_id", "=", self.env.company.id),
            ("state", "=", "done"),
        ]
        if to_date:
            move_domain.append(("date", "<=", to_date))
        moves = self.env["stock.move"].search(move_domain)
        action = self.env["ir.actions.actions"]._for_xml_id("stock.stock_move_action")
        action["domain"] = [("id", "in", moves.ids)]
        return action
