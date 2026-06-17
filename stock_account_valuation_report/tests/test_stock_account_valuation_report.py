# 2020 Copyright ForgeFlow, S.L. (https://www.forgeflow.com)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).


from odoo import Command
from odoo.fields import Domain

from odoo.addons.stock_account.tests.common import TestStockValuationCommon


class TestStockAccountValuationReport(TestStockValuationCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aml_model = cls.env["account.move.line"]
        cls.general_journal = cls.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", cls.env.company.id)],
            limit=1,
        )

    def _post_valuation_entry(self, product, account, debit, credit, quantity):
        """Post a balanced journal entry whose valuation-account line carries
        the given product quantity and direction.

        Mirrors the two ways Odoo 19 populates the valuation account:
        invoice/COGS entries store an *unsigned* quantity (direction only in
        debit/credit), while stock-move entries store a *signed* one.
        """
        counterpart = self.env["account.account"].search(
            [("account_type", "=", "expense")],
            limit=1,
        )
        move = self.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": self.general_journal.id,
                "line_ids": [
                    Command.create(
                        {
                            "account_id": account.id,
                            "product_id": product.id,
                            "quantity": quantity,
                            "debit": debit,
                            "credit": credit,
                            "name": "valuation",
                        }
                    ),
                    Command.create(
                        {
                            "account_id": counterpart.id,
                            "debit": credit,
                            "credit": debit,
                            "name": "counterpart",
                        }
                    ),
                ],
            }
        )
        move.action_post()
        return move

    def test_01_stock_receipt(self):
        """Receive into stock and ship to the customer"""
        self._use_inventory_location_accounting()
        move = self._make_in_move(self.product_fifo_auto, quantity=1, unit_cost=10.0)
        inv_aml = self.aml_model.search(
            Domain("product_id", "=", self.product_fifo_auto.id)
        )
        balance_inv = sum(inv_aml.mapped("balance"))
        self.assertEqual(balance_inv, 0.0)
        self.assertEqual(move.remaining_value, 10.0)
        # The accounting value and the stock value do not match
        self.assertEqual(self.product_fifo_auto.stock_value, 10.0)
        self.assertEqual(self.product_fifo_auto.account_value, 0.0)
        self.assertEqual(self.product_fifo_auto.qty_at_date, 1.0)
        self.assertEqual(self.product_fifo_auto.account_qty_at_date, 0.0)
        # Stock Move can be opened from the product
        action = self.product_fifo_auto.action_view_valuation_moves()
        self.assertEqual(
            self.env[action["res_model"]].search(action["domain"]),
            move,
        )
        # Create an out move
        move_out = self._make_out_move(self.product_fifo_auto, 1.0)
        self.assertEqual(move.remaining_qty, 0.0)
        self.assertEqual(move.remaining_value, 0.0)
        self.assertEqual(move_out.value, 10.0)
        # The report shows the material is gone
        self.product_fifo_auto._compute_inventory_value()
        self.assertEqual(self.product_fifo_auto.stock_value, 0.0)
        self.assertEqual(self.product_fifo_auto.account_value, 0.0)
        self.assertEqual(self.product_fifo_auto.qty_at_date, 0.0)
        self.assertEqual(self.product_fifo_auto.account_qty_at_date, 0.0)

    def test_02_stock_receipt_several_costs_several_dates(self):
        """Receive into stock at different cost"""
        self._use_inventory_location_accounting()
        move = self._make_in_move(self.product_fifo_auto, quantity=1.0, unit_cost=10.0)
        # This will not create any journal entry
        inv_aml = self.aml_model.search(
            Domain("product_id", "=", self.product_fifo_auto.id)
        )
        balance_inv = sum(inv_aml.mapped("balance"))
        self.assertEqual(balance_inv, 0.0)
        self.assertEqual(move.remaining_value, 10.0)
        # Receive more
        move2 = self._make_in_move(self.product_fifo_auto, quantity=2.0, unit_cost=20.0)
        # This will not create any journal entry
        inv_aml = self.aml_model.search(
            Domain("product_id", "=", self.product_fifo_auto.id)
        )
        balance_inv = sum(inv_aml.mapped("balance"))
        self.assertEqual(balance_inv, 0.0)
        self.assertEqual(move2.remaining_value, 40.0)
        # Now we check the report reflects the same
        self.assertEqual(self.product_fifo_auto.stock_value, 50.0)
        self.assertEqual(self.product_fifo_auto.account_value, 0.0)
        self.assertEqual(self.product_fifo_auto.qty_at_date, 3.0)
        self.assertEqual(self.product_fifo_auto.account_qty_at_date, 0.0)

    def test_03_stock_receipt_with_invoice(self):
        """Receive into stock, create bill, ship to customer with invoice"""
        self._use_inventory_location_accounting()
        self._make_in_move(self.product_fifo_auto, 1, unit_cost=10.0)
        self.product_fifo_auto._compute_inventory_value()
        self.assertEqual(self.product_fifo_auto.stock_value, 10.0)
        self.assertEqual(self.product_fifo_auto.account_value, 0.0)
        self.assertEqual(self.product_fifo_auto.qty_at_date, 1.0)
        self.assertEqual(self.product_fifo_auto.account_qty_at_date, 0.0)
        self.assertEqual(self.product_fifo_auto.valuation_discrepancy, 10.0)
        self._create_bill(self.product_fifo_auto, 1.0, price_unit=10.0)
        self.product_fifo_auto._compute_inventory_value()
        self.assertEqual(self.product_fifo_auto.stock_value, 10.0)
        self.assertEqual(self.product_fifo_auto.account_value, 10.0)
        self.assertEqual(self.product_fifo_auto.qty_at_date, 1.0)
        self.assertEqual(self.product_fifo_auto.account_qty_at_date, 1.0)
        self.assertEqual(self.product_fifo_auto.valuation_discrepancy, 0.0)
        self.assertEqual(self.product_fifo_auto.qty_discrepancy, 0.0)
        # check accounting entries
        inv_aml = self.aml_model.search(
            Domain("product_id", "=", self.product_fifo_auto.id)
        )
        self.assertEqual(sum(inv_aml.mapped("balance")), 10.0)
        self.assertEqual(sum(inv_aml.mapped("quantity")), 1.0)
        self._make_out_move(self.product_fifo_auto, 1.0)
        # Before customer invoice: stock is empty but accounting still shows value
        self.product_fifo_auto._compute_inventory_value()
        self.assertEqual(self.product_fifo_auto.stock_value, 0.0)
        self.assertEqual(self.product_fifo_auto.account_value, 10.0)
        self.assertEqual(self.product_fifo_auto.qty_at_date, 0.0)
        self.assertEqual(self.product_fifo_auto.account_qty_at_date, 1.0)
        self.assertEqual(self.product_fifo_auto.valuation_discrepancy, -10.0)

        self._create_invoice(self.product_fifo_auto, 1.0, price_unit=10.0)
        # After customer invoice: everything should be zero
        self.product_fifo_auto._compute_inventory_value()
        self.assertEqual(self.product_fifo_auto.stock_value, 0.0)
        self.assertEqual(self.product_fifo_auto.account_value, 0.0)
        self.assertEqual(self.product_fifo_auto.qty_at_date, 0.0)
        self.assertEqual(self.product_fifo_auto.account_qty_at_date, 0.0)
        self.assertEqual(self.product_fifo_auto.valuation_discrepancy, 0.0)

        # Verify final accounting balance is zero
        inv_aml = self.aml_model.search(
            Domain("product_id", "=", self.product_fifo_auto.id)
        )
        self.assertEqual(sum(inv_aml.mapped("balance")), 0.0)

    def test_04_accounting_qty_mixed_quantity_sign(self):
        """Accounting quantity must net incoming against outgoing regardless of
        how each valuation line signed its quantity.

        On the valuation account Odoo 19 mixes two conventions: invoice/COGS
        lines store an unsigned quantity (direction only in debit/credit) while
        stock-move lines store a signed one. Receiving 10 then shipping 4 via a
        COGS line (unsigned +4) and 4 via a stock-move line (signed -4) must
        leave an accounting quantity of 2 -- the on-hand. Trusting the stored
        sign double-counts one of the two outgoing lines.
        """
        product = self.product_fifo_auto
        valuation_account = product._get_product_accounts()["stock_valuation"]
        # Incoming 10 (debit, unsigned +10)
        self._post_valuation_entry(product, valuation_account, 1000.0, 0.0, 10.0)
        # Outgoing 4 via COGS convention (credit, unsigned +4)
        self._post_valuation_entry(product, valuation_account, 0.0, 400.0, 4.0)
        # Outgoing 4 via stock-move convention (credit, signed -4)
        self._post_valuation_entry(product, valuation_account, 0.0, 400.0, -4.0)
        product._compute_inventory_value()
        self.assertEqual(product.account_qty_at_date, 2.0)

    def test_05_qty_discrepancy_search_nets_signed_outgoing(self):
        """The qty-discrepancy search must net a signed outgoing line, not add it.

        Receiving 4 then shipping 4 via a stock-move line (signed -4) leaves an
        accounting quantity of 0, matching the (empty) stock side, so the
        product is NOT discrepant and must be excluded from the search. The old
        expression added the signed line instead of subtracting it, computing 8
        and wrongly flagging the product. This drives the search path
        (_get_discrepancy_product_ids), distinct from the compute above.
        """
        product = self.product_fifo_auto
        valuation_account = product._get_product_accounts()["stock_valuation"]
        # Incoming 4 (debit, unsigned +4)
        self._post_valuation_entry(product, valuation_account, 400.0, 0.0, 4.0)
        # Outgoing 4 via stock-move convention (credit, signed -4)
        self._post_valuation_entry(product, valuation_account, 0.0, 400.0, -4.0)
        discrepant = self.env["product.product"].search(
            Domain("qty_discrepancy", "!=", 0)
        )
        self.assertNotIn(product, discrepant)
