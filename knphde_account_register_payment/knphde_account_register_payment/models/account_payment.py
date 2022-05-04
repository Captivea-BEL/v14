
import datetime

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AccountPayment(models.Model):
    
    _inherit = 'account.payment'
    _check_company_auto = False
    
    intercompany_move_ids = fields.Many2many("account.move", 'acc_mv_acc_pmnt_rel', 'account_payment_id', 'account_move_id', string="Intercompany Journal Entry")
    
    def unlink(self):
        # OVERRIDE to unlink the inherited account.move (move_id field) as well. Cancel the intercompany
        # moves as well and reset the bills/invoices to posted state
        # moves = self.with_context(force_delete=True).move_id
        intercompany_moves = self.with_context(force_delete=True).intercompany_move_ids
        res = super().unlink()
        if intercompany_moves:
            for int_move in intercompany_moves:
                bill_reference_list = int_move.ref.split(' ')
                for ref in bill_reference_list:
                    inv_bill_id = self.env['account.move'].search(['|',('ref','=', ref),('name','=',ref)], limit=1)
                    if inv_bill_id:
                        inv_bill_id.button_draft()
                        inv_bill_id.action_post()
                int_move.button_draft()
                int_move.button_cancel()
        return res

    def _synchronize_from_moves(self, changed_fields):
        ''' Update the account.payment regarding its related account.move.
        Also, check both models are still consistent.
        :param changed_fields: A set containing all modified fields on account.move.
        '''
        if self._context.get('skip_account_move_synchronization'):
            return

        for pay in self.with_context(skip_account_move_synchronization=True):

            # After the migration to 14.0, the journal entry could be shared between the account.payment and the
            # account.bank.statement.line. In that case, the synchronization will only be made with the statement line.
            if pay.move_id.statement_line_id:
                continue

            move = pay.move_id
            move_vals_to_write = {}
            payment_vals_to_write = {}

            if 'journal_id' in changed_fields:
                if pay.journal_id.type not in ('bank', 'cash'):
                    raise UserError(_("A payment must always belongs to a bank or cash journal."))

            if 'line_ids' in changed_fields:
                all_lines = move.line_ids
                liquidity_lines, counterpart_lines, writeoff_lines = pay._seek_for_lines()
                # if len(liquidity_lines) != 1 or len(counterpart_lines) != 1:
                #     raise UserError(_(
                #         "The journal entry %s reached an invalid state relative to its payment.\n"
                #         "To be consistent, the journal entry must always contains:\n"
                #         "- one journal item involving the outstanding payment/receipts account.\n"
                #         "- one journal item involving a receivable/payable account.\n"
                #         "- optional journal items, all sharing the same account.\n\n"
                #     ) % move.display_name)

                # if writeoff_lines and len(writeoff_lines.account_id) != 1:
                #     raise UserError(_(
                #         "The journal entry %s reached an invalid state relative to its payment.\n"
                #         "To be consistent, all the write-off journal items must share the same account."
                #     ) % move.display_name)

                if any(line.currency_id != all_lines[0].currency_id for line in all_lines):
                    raise UserError(_(
                        "The journal entry %s reached an invalid state relative to its payment.\n"
                        "To be consistent, the journal items must share the same currency."
                    ) % move.display_name)

                if any(line.partner_id != all_lines[0].partner_id for line in all_lines):
                    raise UserError(_(
                        "The journal entry %s reached an invalid state relative to its payment.\n"
                        "To be consistent, the journal items must share the same partner."
                    ) % move.display_name)

                if counterpart_lines[0].account_id.user_type_id.type == 'receivable':
                    partner_type = 'customer'
                else:
                    partner_type = 'supplier'

                liquidity_amount = liquidity_lines.amount_currency
                move_vals_to_write.update({
                    'currency_id': liquidity_lines.currency_id.id,
                    'partner_id': liquidity_lines.partner_id.id,
                })
                payment_vals_to_write.update({
                    'amount': abs(liquidity_amount),
                    'payment_type': 'inbound' if liquidity_amount > 0.0 else 'outbound',
                    'partner_type': partner_type,
                    'currency_id': liquidity_lines.currency_id.id,
                    'destination_account_id': counterpart_lines[0].account_id.id,
                    'partner_id': liquidity_lines.partner_id.id,
                })

            move.write(move._cleanup_write_orm_values(move, move_vals_to_write))
            pay.write(move._cleanup_write_orm_values(pay, payment_vals_to_write))
    
    def _prepare_move_line_default_vals(self, write_off_line_vals=None):
        ''' Prepare the dictionary to create the default account.move.lines for the current payment.
        :param write_off_line_vals: Optional dictionary to create a write-off account.move.line easily containing:
            * amount:       The amount to be added to the counterpart amount.
            * name:         The label to set on the line.
            * account_id:   The account on which create the write-off.
        :return: A list of python dictionary to be passed to the account.move.line's 'create' method.
        '''
        self.ensure_one()
        write_off_line_vals = write_off_line_vals or {}

        if not self.journal_id.payment_debit_account_id or not self.journal_id.payment_credit_account_id:
            raise UserError(_(
                "You can't create a new payment without an outstanding payments/receipts account set on the %s journal.",
                self.journal_id.display_name))

        # Compute amounts.
        write_off_amount = write_off_line_vals.get('amount', 0.0)

        if self.payment_type == 'inbound':
            # Receive money.
            counterpart_amount = -self.amount
            write_off_amount *= -1
        elif self.payment_type == 'outbound':
            # Send money.
            counterpart_amount = self.amount
        else:
            counterpart_amount = 0.0
            write_off_amount = 0.0

        balance = self.currency_id._convert(counterpart_amount, self.company_id.currency_id, self.company_id, self.date)
        counterpart_amount_currency = counterpart_amount
        write_off_balance = self.currency_id._convert(write_off_amount, self.company_id.currency_id, self.company_id, self.date)
        write_off_amount_currency = write_off_amount
        currency_id = self.currency_id.id
        
        if self.is_internal_transfer:
            if self.payment_type == 'inbound':
                liquidity_line_name = _('Transfer to %s', self.journal_id.name)
            else: # payment.payment_type == 'outbound':
                liquidity_line_name = _('Transfer from %s', self.journal_id.name)
        else:
            liquidity_line_name = self.payment_reference

        # Compute a default label to set on the journal items.

        payment_display_name = {
            'outbound-customer': _("Customer Reimbursement"),
            'inbound-customer': _("Customer Payment"),
            'outbound-supplier': _("Vendor Payment"),
            'inbound-supplier': _("Vendor Reimbursement"),
        }

        default_line_name = self.env['account.move.line']._get_default_line_name(
            _("Internal Transfer") if self.is_internal_transfer else payment_display_name['%s-%s' % (self.payment_type, self.partner_type)],
            self.amount,
            self.currency_id,
            self.date,
            partner=self.partner_id,
        )
        line_vals_list = [
            # Liquidity line.
            {
                'name': liquidity_line_name or default_line_name,
                'date_maturity': self.date,
                'amount_currency': -counterpart_amount_currency,
                'currency_id': currency_id,
                'debit': balance < 0.0 and -balance or 0.0,
                'credit': balance > 0.0 and balance or 0.0,
                'partner_id': self.partner_id.id,
                'account_id': self.journal_id.payment_debit_account_id.id if balance < 0.0 else self.journal_id.payment_credit_account_id.id,
            },
            # Receivable / Payable.
            {
                'name': self.payment_reference or default_line_name,
                'date_maturity': self.date,
                'amount_currency': counterpart_amount_currency + write_off_amount_currency if currency_id else 0.0,
                'currency_id': currency_id,
                'debit': balance + write_off_balance > 0.0 and balance + write_off_balance or 0.0,
                'credit': balance + write_off_balance < 0.0 and -balance - write_off_balance or 0.0,
                'partner_id': self.partner_id.id,
                'account_id': self.destination_account_id.id,
            },
        ]
        if self.intercompany_move_ids:
            line_vals_list = []
            intercompany_amount_total = 0.0
            for intcmp_move in self.intercompany_move_ids:
                intercompany_amount_total += intcmp_move.amount_total
            if self.payment_type == 'inbound':
                if intercompany_amount_total != balance:
                    ar_ap_balance = -(abs(balance) - intercompany_amount_total)
                elif intercompany_amount_total == balance:
                    ar_ap_balance = -(abs(balance) - intercompany_amount_total)
                intercompany_amount_total = -(intercompany_amount_total)
            else:
                if intercompany_amount_total != balance:
                    ar_ap_balance = abs(balance) - intercompany_amount_total
                elif intercompany_amount_total == balance:
                    ar_ap_balance = abs(balance) - intercompany_amount_total
            intercompany_account_id = self.env['account.account'].search([('name','=','Intercompany'),('company_id','=',self.company_id.id)], limit=1)
            if not intercompany_account_id:
                raise UserError(_("Please create Intercompany account for %s company",self.company_id.name))
            line_vals_list = [
                # Liquidity line.
                {
                    'name': liquidity_line_name or default_line_name,
                    'date_maturity': self.date,
                    # 'amount_currency': ar_ap_balance + write_off_amount_currency if currency_id else 0.0,
                    # 'currency_id': currency_id,
                    # 'debit': ar_ap_balance + write_off_balance < 0.0 and -ar_ap_balance - write_off_balance or 0.0,
                    # 'credit': ar_ap_balance + write_off_balance > 0.0 and ar_ap_balance + write_off_balance or 0.0,
                    'amount_currency': -counterpart_amount_currency,
                    'currency_id': currency_id,
                    'debit': balance < 0.0 and -balance or 0.0,
                    'credit': balance > 0.0 and balance or 0.0,
                    'partner_id': self.partner_id.id,
                    'account_id': self.journal_id.payment_debit_account_id.id if balance < 0.0 else self.journal_id.payment_credit_account_id.id,
                },
                # Receivable / Payable.
                {
                    'name': self.payment_reference or default_line_name,
                    'date_maturity': self.date,
                    'amount_currency': ar_ap_balance + write_off_amount_currency if currency_id else 0.0,
                    'currency_id': currency_id,
                    'debit': ar_ap_balance + write_off_balance > 0.0 and ar_ap_balance + write_off_balance or 0.0,
                    'credit': ar_ap_balance + write_off_balance < 0.0 and -ar_ap_balance - write_off_balance or 0.0,
                    # 'amount_currency': -counterpart_amount_currency,
                    # 'currency_id': currency_id,
                    # 'debit': balance > 0.0 and balance or 0.0,
                    # 'credit': balance < 0.0 and -balance or 0.0,
                    'partner_id': self.partner_id.id,
                    'account_id': self.destination_account_id.id,
                },
                {
                    'name': self.payment_reference or default_line_name,
                    'date_maturity': self.date,
                    'amount_currency': intercompany_amount_total + write_off_amount_currency if currency_id else 0.0,
                    'currency_id': currency_id,
                    # 'debit': intercompany_amount_total + write_off_balance > 0.0 and intercompany_amount_total + write_off_balance or 0.0,
                    # 'credit': intercompany_amount_total + write_off_balance < 0.0 and -intercompany_amount_total - write_off_balance or 0.0,
                    'debit': intercompany_amount_total > 0.0 and intercompany_amount_total or 0.0,
                    'credit': intercompany_amount_total < 0.0 and -intercompany_amount_total or 0.0,
                    'partner_id': self.partner_id.id,
                    'account_id': intercompany_account_id.id,
                },
            ]
        if write_off_balance:
            # Write-off line.
            line_vals_list.append({
                'name': write_off_line_vals.get('name') or default_line_name,
                'amount_currency': -write_off_amount_currency,
                'currency_id': currency_id,
                'debit': write_off_balance < 0.0 and -write_off_balance or 0.0,
                'credit': write_off_balance > 0.0 and write_off_balance or 0.0,
                'partner_id': self.partner_id.id,
                'account_id': write_off_line_vals.get('account_id'),
            })
        return line_vals_list
    
    
    def action_cancel(self):
        ''' draft -> cancelled '''
        intercompany_moves = self.with_context(force_delete=True).intercompany_move_ids
        res = super().action_cancel()
        if intercompany_moves:
            for int_move in intercompany_moves:
                bill_reference_list = int_move.ref.split(' ')
                for ref in bill_reference_list:
                    inv_bill_id = self.env['account.move'].search(['|',('ref','=', ref),('name','=',ref)], limit=1)
                    if inv_bill_id:
                        inv_bill_id.button_draft()
                        inv_bill_id.action_post()
                int_move.button_draft()
                int_move.button_cancel()
        return res

    def action_draft(self):
        ''' posted -> draft '''
        intercompany_moves = self.with_context(force_delete=True).intercompany_move_ids
        res = super().action_draft()
        if intercompany_moves:
            for int_move in intercompany_moves:
                # bill_reference_list = int_move.ref.split(' ')
                # for ref in bill_reference_list:
                #     inv_bill_id = self.env['account.move'].search(['|',('ref','=', ref),('name','=',ref)], limit=1)
                #     if inv_bill_id:
                #         inv_bill_id.button_draft()
                #         inv_bill_id.action_post()
                int_move.button_draft()
        return res
    
    # def action_post(self):
    #     intercompany_moves = self.with_context(force_delete=True).intercompany_move_ids
    #     res = super().action_post()
    #     if intercompany_moves:
    #         for int_move in intercompany_moves:
    #             int_move._post(soft=False)
    #     return res
    
    def action_reverse_entry(self):
        action = self.env["ir.actions.actions"]._for_xml_id("account.action_view_account_move_reversal") 

        # if self.is_invoice():
        #     action['name'] = _('Credit Note')
        reverse_jl_ids_list = []
        if self.move_id:
            reverse_jl_ids_list.append(self.move_id.id)
            
        if self.intercompany_move_ids:
            for intercomp_move_id in self.intercompany_move_ids:
                reverse_jl_ids_list.append(intercomp_move_id.id)
        if reverse_jl_ids_list:
            action['context'] = {'payment_move_ids':[(6, 0,reverse_jl_ids_list)]}
        
        return action
    

class AccountAccount(models.Model):
    
    _inherit = 'account.account'
    _check_company_auto = False

class AccountMoveLine(models.Model):
    
    _inherit = 'account.move.line'
    _check_company_auto = False
    
    payment_term_discount_inclusion = fields.Boolean(string="Exclude Discount?",help="Check True if the Payment Term does NOT apply for this product/line")
    
    _sql_constraints = [(
            'check_amount_currency_balance_sign',
            '''CHECK(
                1=1)''',
            "The amount expressed in the secondary currency must be positive when account is debited and negative when "
            "account is credited. If the currency is the same as the one from the company, this amount must strictly "
            "be equal to the balance."
        ),]
    
    def reconcile(self):
        ''' Reconcile the current move lines all together.
        :return: A dictionary representing a summary of what has been done during the reconciliation:
                * partials:             A recorset of all account.partial.reconcile created during the reconciliation.
                * full_reconcile:       An account.full.reconcile record created when there is nothing left to reconcile
                                        in the involved lines.
                * tax_cash_basis_moves: An account.move recordset representing the tax cash basis journal entries.
        '''
        results = {}

        if not self:
            return results

        # List unpaid invoices
        not_paid_invoices = self.move_id.filtered(
            lambda move: move.is_invoice(include_receipts=True) and move.payment_state not in ('paid', 'in_payment')
        )

        # ==== Check the lines can be reconciled together ====
        company = None
        account = None
        for line in self:
            if line.reconciled:
                raise UserError(_("You are trying to reconcile some entries that are already reconciled."))
            if not line.account_id.reconcile and line.account_id.internal_type != 'liquidity':
                raise UserError(_("Account %s does not allow reconciliation. First change the configuration of this account to allow it.")
                                % line.account_id.display_name)
            if line.move_id.state != 'posted':
                raise UserError(_('You can only reconcile posted entries.'))
            if company is None:
                company = line.company_id
            elif line.company_id != company:
                # raise UserError(_("Entries doesn't belong to the same company: %s != %s")
                #                 % (company.display_name, line.company_id.display_name))
                pass
            if account is None:
                account = line.account_id
            # elif line.account_id != account:
            #     raise UserError(_("Entries are not from the same account: %s != %s")
            #                     % (account.display_name, line.account_id.display_name))

        sorted_lines = self.sorted(key=lambda line: (line.date_maturity or line.date, line.currency_id))

        # ==== Collect all involved lines through the existing reconciliation ====
        involved_lines = sorted_lines
        involved_partials = self.env['account.partial.reconcile']
        current_lines = involved_lines
        current_partials = involved_partials
        while current_lines:
            current_partials = (current_lines.matched_debit_ids + current_lines.matched_credit_ids) - current_partials
            involved_partials += current_partials
            current_lines = (current_partials.debit_move_id + current_partials.credit_move_id) - current_lines
            involved_lines += current_lines

        # ==== Create partials ====
        partials = self.env['account.partial.reconcile'].create(sorted_lines._prepare_reconciliation_partials())

        # Track newly created partials.
        results['partials'] = partials
        involved_partials += partials

        # ==== Create entries for cash basis taxes ====

        is_cash_basis_needed = account.user_type_id.type in ('receivable', 'payable')
        if is_cash_basis_needed and not self._context.get('move_reverse_cancel'):
            tax_cash_basis_moves = partials._create_tax_cash_basis_moves()
            results['tax_cash_basis_moves'] = tax_cash_basis_moves

        # ==== Check if a full reconcile is needed ====

        if involved_lines[0].currency_id and all(line.currency_id == involved_lines[0].currency_id for line in involved_lines):
            is_full_needed = all(line.currency_id.is_zero(line.amount_residual_currency) for line in involved_lines)
        else:
            is_full_needed = all(line.company_currency_id.is_zero(line.amount_residual) for line in involved_lines)
        if is_full_needed:

            # ==== Create the exchange difference move ====

            if self._context.get('no_exchange_difference'):
                exchange_move = None
            else:
                exchange_move = involved_lines._create_exchange_difference_move()
                if exchange_move:
                    exchange_move_lines = exchange_move.line_ids.filtered(lambda line: line.account_id == account)

                    # Track newly created lines.
                    involved_lines += exchange_move_lines

                    # Track newly created partials.
                    exchange_diff_partials = exchange_move_lines.matched_debit_ids \
                                             + exchange_move_lines.matched_credit_ids
                    involved_partials += exchange_diff_partials
                    results['partials'] += exchange_diff_partials

                    exchange_move._post(soft=False)

            # ==== Create the full reconcile ====

            results['full_reconcile'] = self.env['account.full.reconcile'].create({
                'exchange_move_id': exchange_move and exchange_move.id,
                'partial_reconcile_ids': [(6, 0, involved_partials.ids)],
                'reconciled_line_ids': [(6, 0, involved_lines.ids)],
            })
        # kfvmdflkmlv
        # Trigger action for paid invoices
        not_paid_invoices\
            .filtered(lambda move: move.payment_state in ('paid', 'in_payment'))\
            .action_invoice_paid()

        return results


class AccountMove(models.Model):
    
    _inherit = "account.move"
    _check_company_auto = False
    
    due_today = fields.Monetary(compute="_compute_due_today", string="Due Today")
    days = fields.Integer(compute='_compute_days',string="days")
    
    # def _get_reconciled_info_JSON_values(self):
    #     self.ensure_one()
    #     counterpart_line_ids_list = []
    #     reconciled_vals = []
    #     for partial, amount, counterpart_line in self._get_reconciled_invoices_partials():
    #         if counterpart_line.id not in counterpart_line_ids_list:
    #             if counterpart_line.move_id.ref:
    #                 reconciliation_ref = '%s (%s)' % (counterpart_line.move_id.name, counterpart_line.move_id.ref)
    #             else:
    #                 reconciliation_ref = counterpart_line.move_id.name
    #
    #             reconciled_vals.append({
    #                 'name': counterpart_line.name,
    #                 'journal_name': counterpart_line.journal_id.name,
    #                 'amount': amount,
    #                 'currency': self.currency_id.symbol,
    #                 'digits': [69, self.currency_id.decimal_places],
    #                 'position': self.currency_id.position,
    #                 'date': counterpart_line.date,
    #                 'payment_id': counterpart_line.id,
    #                 'partial_id': partial.id,
    #                 'account_payment_id': counterpart_line.payment_id.id,
    #                 'payment_method_name': counterpart_line.payment_id.payment_method_id.name if counterpart_line.journal_id.type == 'bank' else None,
    #                 'move_id': counterpart_line.move_id.id,
    #                 'ref': reconciliation_ref,
    #             })
    #         if counterpart_line.id in counterpart_line_ids_list and len(reconciled_vals) == 1:
    #             reconciled_vals[0]['amount'] = reconciled_vals[0].get('amount',0) + amount
    #         counterpart_line_ids_list.append(counterpart_line.id)
    #     return reconciled_vals

    @api.depends('invoice_payment_term_id', 'amount_residual', 'invoice_date', 'invoice_line_ids')
    def _compute_days(self):
        for record in self:
            if record.invoice_payment_term_id and record.invoice_date:
                days=datetime.date.today()-record.invoice_date
                days=days.days
                for lne in record.invoice_payment_term_id.line_ids:
                    if lne.value=='percent':
                        days=lne.days - abs(days)
                record.days = days
            else:
                record.days = False
    
    @api.depends('invoice_payment_term_id','amount_residual','invoice_date','invoice_line_ids')
    def _compute_due_today(self):
        for record in self:
            perc=100
            if record.invoice_payment_term_id and record.invoice_date:
                # days=datetime.date.today()-record.invoice_date
                days= record.days
                for lne in record.invoice_payment_term_id.line_ids:
                    if days <= lne.days and not days <= 0 and lne.value=='percent':
                        perc=lne.value_amount
                total_amnt = 0
                for lne in record.invoice_line_ids:
                    amnt = 0
                    if not lne.payment_term_discount_inclusion:
                        amnt=amnt+(lne.price_subtotal*perc/100)
                        if lne.tax_ids:
                            for tx in lne.tax_ids:
                                amnt += (amnt*tx.amount/100)
                        total_amnt += amnt
                    else:
                        amnt=amnt+lne.price_subtotal
                        if lne.tax_ids:
                            for tx in lne.tax_ids:
                                amnt += (amnt*tx.amount/100)
                        total_amnt += amnt
                if total_amnt > record.amount_residual:
                    total_amnt=record.amount_residual
                record.due_today = total_amnt
            else:
                record.due_today = False
    
    
    
    
    
    