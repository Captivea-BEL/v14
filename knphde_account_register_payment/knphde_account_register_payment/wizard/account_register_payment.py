
from odoo import models, fields, api, _
from datetime import datetime
from odoo.exceptions import UserError


class AccountPaymentRegister(models.TransientModel):
    
    _inherit = "account.payment.register"
    
    payment_type = fields.Selection([
        ('outbound', 'Send Money'),
        ('inbound', 'Receive Money'),
    ], string='Payment Type', store=True, copy=False,
        compute='_compute_from_lines')
    partner_type = fields.Selection([
        ('customer', 'Customer'),
        ('supplier', 'Vendor'),
    ], store=True, copy=False,
        compute='_compute_from_lines')
    source_amount = fields.Monetary(
        string="Amount to Pay (company currency)", store=True, copy=False,
        currency_field='company_currency_id',
        compute='_compute_from_lines')
    source_amount_currency = fields.Monetary(
        string="Amount to Pay (foreign currency)", store=True, copy=False,
        currency_field='source_currency_id',
        compute='_compute_from_lines')
    source_currency_id = fields.Many2one('res.currency',
        string='Source Currency', store=True, copy=False,
        compute='_compute_from_lines',
        help="The payment's currency.")
    can_edit_wizard = fields.Boolean(store=True, copy=False,
        compute='_compute_from_lines',
        help="Technical field used to indicate the user can edit the wizard content such as the amount.")
    can_group_payments = fields.Boolean(store=True, copy=False,
        compute='_compute_from_lines',
        help="Technical field used to indicate the user can see the 'group_payments' box.")
    # company_id = fields.Many2one('res.company', store=True, copy=False,
    #     compute='_compute_from_lines')
    company_id = fields.Many2one('res.company', required=True, store=True, copy=False, compute=False, domain = lambda self: "[('id','in',%s)]" % self._context.get('allowed_company_ids'))
    partner_id = fields.Many2one('res.partner',
        string="Customer/Vendor", store=True, copy=False, ondelete='restrict',
        compute='_compute_from_lines')
    journal_id = fields.Many2one('account.journal', store=True, readonly=False,
        compute=False,
        domain="[('company_id', '=', company_id), ('type', 'in', ('bank', 'cash'))]")
    group_payment = fields.Boolean(string="Group Payments", store=True, readonly=True,
        compute='_compute_group_payment',
        help="Only one payment will be created by partner (bank)/ currency.")

    invoice_bill_ids = fields.Many2many("unique.invoice.bills",'unique_invoice_bills_acc_pay_reg_rel','invoice_bill_id','account_payment_register_id',"Unique Invoices/Bills")

    
    # @api.depends('can_edit_wizard')
    # def _compute_group_payment(self):
    #     for wizard in self:
    #         if wizard.can_edit_wizard:
    #             batches = wizard._get_batches()
    #             wizard.group_payment = len(batches[0]['lines'].move_id) >= 1
    #         else:
    #             wizard.group_payment = False
    
    @api.depends('source_amount', 'source_amount_currency', 'source_currency_id', 'company_id', 'currency_id', 'payment_date')
    def _compute_amount(self):
        for wizard in self:
            if wizard.invoice_bill_ids:
                amount = 0
                for inv_bill in wizard.invoice_bill_ids:
                    amount += inv_bill.total_amount
                wizard.amount = amount
            else:
                if wizard.source_currency_id == wizard.currency_id:
                    # Same currency.
                    wizard.amount = wizard.source_amount_currency
                elif wizard.currency_id == wizard.company_id.currency_id:
                    # Payment expressed on the company's currency.
                    wizard.amount = wizard.source_amount
                else:
                    # Foreign currency on payment different than the one set on the journal entries.
                    amount_payment_currency = wizard.company_id.currency_id._convert(wizard.source_amount, wizard.currency_id, wizard.company_id, wizard.payment_date)
                    wizard.amount = amount_payment_currency
    
    @api.depends('can_edit_wizard')
    def _compute_group_payment(self):
        for wizard in self:
            if wizard.can_edit_wizard:
                batches = wizard._get_batches()
                wizard.group_payment = len(batches[0]['lines'].move_id) >= 1
            else:
                wizard.group_payment = True

    def _get_batches(self):
        ''' Group the account.move.line linked to the wizard together.
        :return: A list of batches, each one containing:
            * key_values:   The key as a dictionary used to group the journal items together.
            * moves:        An account.move recordset.
        '''
        self.ensure_one()

        lines = self.line_ids._origin

        # if len(lines.company_id) > 1:
        #     raise UserError(_("You can't create payments for entries belonging to different companies."))
        if not lines:
            raise UserError(_("You can't open the register payment wizard without at least one receivable/payable line."))

        batches = {}
        for line in lines:
            batch_key = self._get_line_batch_key(line)
            # serialized_key = '-'.join(str(v) for v in batch_key.values())
            serialized_key = '-'.join(str(v) for k,v in batch_key.items() if not k == 'account_id' and not k == 'currency_id')
            batches.setdefault(serialized_key, {
                'key_values': batch_key,
                'lines': self.env['account.move.line'],
            })
            batches[serialized_key]['lines'] += line
        return list(batches.values())

    @api.model
    def _get_wizard_values_from_batch(self, batch_result):
        ''' Extract values from the batch passed as parameter (see '_get_batches')
        to be mounted in the wizard view.
        :param batch_result:    A batch returned by '_get_batches'.
        :return:                A dictionary containing valid fields
        '''
        key_values = batch_result['key_values']
        lines = batch_result['lines']
        company = lines[0].company_id

        source_amount = abs(sum(lines.mapped('amount_residual')))
        if key_values['currency_id'] == company.currency_id.id:
            source_amount_currency = source_amount
        else:
            source_amount_currency = abs(sum(lines.mapped('amount_residual_currency')))

        return {
            # 'company_id': company.id,
            'partner_id': key_values['partner_id'],
            'partner_type': key_values['partner_type'],
            'payment_type': key_values['payment_type'],
            'source_currency_id': key_values['currency_id'],
            'source_amount': source_amount,
            'source_amount_currency': source_amount_currency,
        }

    @api.depends('line_ids')
    def _compute_from_lines(self):
        ''' Load initial values from the account.moves passed through the context. '''
        for wizard in self:
            batches = wizard._get_batches()
            batch_result = batches[0]
            wizard_values_from_batch = wizard._get_wizard_values_from_batch(batch_result)
            if len(batches) == 1:
                # == Single batch to be mounted on the view ==
                wizard.update(wizard_values_from_batch)

                wizard.can_edit_wizard = True
                wizard.can_group_payments = len(batch_result['lines']) != 1
            else:
                # == Multiple batches: The wizard is not editable  ==
                wizard.update({
                    # 'company_id': batches[0]['lines'][0].company_id.id,
                    'partner_id': False or wizard_values_from_batch['partner_id'],
                    'partner_type': False or wizard_values_from_batch['partner_type'],
                    'payment_type': wizard_values_from_batch['payment_type'],
                    'source_currency_id': False,
                    'source_amount': False,
                    'source_amount_currency': False,
                })
                wizard.can_edit_wizard = False
                wizard.can_group_payments = any(len(batch_result['lines']) != 1 for batch_result in batches)
    
    @api.model
    def default_get(self, fields_list):
        # OVERRIDE
        res = {'payment_date': datetime.now().date(), 'payment_difference_handling': 'open', 'writeoff_label': 'Write-Off'}
        if 'line_ids' in fields_list and 'line_ids' not in res:

            # Retrieve moves to pay from the context.

            if self._context.get('active_model') == 'account.move':
                lines = self.env['account.move'].browse(self._context.get('active_ids', [])).line_ids
            elif self._context.get('active_model') == 'account.move.line':
                lines = self.env['account.move.line'].browse(self._context.get('active_ids', []))
            else:
                raise UserError(_(
                    "The register payment wizard should only be called on account.move or account.move.line records."
                ))

            # Keep lines having a residual amount to pay.
            available_lines = self.env['account.move.line']
            for line in lines:
                if line.move_id.state != 'posted':
                    raise UserError(_("You can only register payment for posted journal entries."))

                if line.account_internal_type not in ('receivable', 'payable'):
                    continue
                if line.currency_id:
                    if line.currency_id.is_zero(line.amount_residual_currency):
                        continue
                else:
                    if line.company_currency_id.is_zero(line.amount_residual):
                        continue
                available_lines |= line
            unique_partner_list = []
            for line in available_lines:
                if line.partner_id.id not in unique_partner_list:
                    unique_partner_list.append(line.partner_id.id)
            partner_dict = {}
            for partner_id in unique_partner_list:
                companylist=[]
                recordlist=[]
                payment_amount = 0
                for line in available_lines:
                    if line.partner_id.id == partner_id:
                        recordlist.append(line.move_id.id)
                        payment_amount += ((line.credit if line.debit == 0 else line.debit) or (line.debit if line.credit == 0 else line.credit))
                        if line.company_id not in companylist:
                            companylist.append(line.company_id.id)
                partner_dict.update({partner_id:{'company_list': companylist, 'record_list': recordlist, 'payment_amount': payment_amount}})
            unique_invoice_bills_ids = self.env['unique.invoice.bills']
            for k, v in partner_dict.items():
                # for comp in :
                move_ids = self.env['account.move'].search([('id', 'in', v['record_list']),('company_id','in',v['company_list'])])
                total_amount = 0.0
                for mv in move_ids:
                    if mv.invoice_payment_term_id:
                        total_amount += mv.due_today
                    else:
                        total_amount += mv.amount_total
                unique_inv_bill_id = self.env['unique.invoice.bills'].create({'partner_id': k, 'account_move_ids': [(6,0,move_ids.ids)], 'company_ids': [(6,0,v['company_list'])], 'total_amount': total_amount})
                unique_invoice_bills_ids |= unique_inv_bill_id
            # Check.
            if not available_lines:
                raise UserError(_("You can't register a payment because there is nothing left to pay on the selected journal items."))
            # if len(lines.company_id) > 1:
            #     raise UserError(_("You can't create payments for entries belonging to different companies."))
            if len(set(available_lines.mapped('account_internal_type'))) > 1:
                raise UserError(_("You can't register payments for journal items being either all inbound, either all outbound."))

            res['line_ids'] = [(6, 0, available_lines.ids)]
            res['invoice_bill_ids'] = [(6, 0, unique_invoice_bills_ids.ids)]
            #customer requested to have company set by default and 'memo' not set by default
            res['company_id'] = self.env.company.id
            if self.payment_difference > 0:
                res['payment_difference_handling'] = 'reconcile'
        # res = super().default_get(fields_list)
        return res

    @api.depends('can_edit_wizard')
    def _compute_communication(self):
        # Override
        # Customer does not want the 'communication field to populate by default
        for wizard in self:
            wizard.communication = False

    # def _custom_create_payment_vals_from_wizard(self, inv_bill):
    #     payment_vals = {
    #         'date': self.payment_date,
    #         'amount': inv_bill.total_amount,
    #         'payment_type': self.payment_type,
    #         'partner_type': self.partner_type,
    #         'ref': self.communication,
    #         'journal_id': self.journal_id.id,
    #         'currency_id': self.currency_id.id,
    #         'partner_id': inv_bill.partner_id.id,
    #         'partner_bank_id': self.partner_bank_id.id,
    #         'payment_method_id': inv_bill.vendor_payment_method.id if self.payment_type == 'outbound' else inv_bill.customer_payment_method.id,
    #         'destination_account_id': self.line_ids[0].account_id.id
    #     }
    #
    #     if not self.currency_id.is_zero(self.payment_difference) and self.payment_difference_handling == 'reconcile':
    #         payment_vals['write_off_line_vals'] = {
    #             'name': self.writeoff_label,
    #             'amount': self.payment_difference,
    #             'account_id': self.writeoff_account_id.id,
    #         }
    #     return payment_vals

    def _create_payments(self):
        self.ensure_one()
        batches = self._get_batches()
        edit_mode = self.can_edit_wizard and (len(batches[0]['lines']) == 1 or self.group_payment)
        to_reconcile = []
        if edit_mode:
            payment_vals = self._create_payment_vals_from_wizard()
            payment_vals_list = [payment_vals]
            to_reconcile.append(batches[0]['lines'])
        else:
            # Don't group payments: Create one batch per move.
            if not self.group_payment:
                new_batches = []
                for batch_result in batches:
                    for line in batch_result['lines']:
                        new_batches.append({
                            **batch_result,
                            'lines': line,
                        })
                batches = new_batches

            payment_vals_list = []
            for batch_result in batches:
                payment_vals_list.append(self._create_payment_vals_from_batch(batch_result))
                to_reconcile.append(batch_result['lines'])

        #create intercompany journal entries if the payment company and journal item entry is different.
        intercompany_journal_entries_list = []
        intercompany_moves = []
        for inv_bill in self.invoice_bill_ids:
            intercompany_companies_list = []
            for comp_id in inv_bill.company_ids:
                if comp_id != self.company_id:
                    intercompany_companies_list.append(comp_id)
            for comp_id in intercompany_companies_list:
                journal_lines = []
                intercompany_total_amount = 0.0
                #Get intercompany amount
                intercompany_move_names_list = []
                intercompany_move_ids = inv_bill.account_move_ids.filtered(lambda mv: mv.company_id == comp_id).sorted(lambda m: m.id)
                intercompany_moves.append(intercompany_move_ids)
                for move in intercompany_move_ids:
                    intercompany_total_amount += move.amount_total
                    if move.ref:
                        intercompany_move_names_list.append(move.ref)
                    else:
                        intercompany_move_names_list.append(move.name)
                intercompany_move_name = ' '.join([nm for nm in intercompany_move_names_list])
                #search intercompany account
                move_type = inv_bill.account_move_ids.mapped('move_type')[0]
                int_comp_acc_id = self.env['account.account'].search([('name','ilike','Intercompany'),('company_id','=',comp_id.id)], limit=1)
                acc_rec_id = self.env['account.account'].search([('name','=','Account Receivable'),('company_id','=',comp_id.id)], limit=1)
                acc_pay_id = self.env['account.account'].search([('name','=','Account Payable'),('company_id','=',comp_id.id)], limit=1)
                int_acc_journal_id = self.env['account.journal'].search([('name','ilike','Intercompany Transactions'),('company_id','=',comp_id.id)], limit=1)
                if not int_comp_acc_id:
                    raise UserError(_("Please create an intercompany account!"))
                if not acc_rec_id:
                    raise UserError(_("Please create a receivable account for %s company!",comp_id.id))
                if not acc_pay_id:
                    raise UserError(_("Please create a payable account for %s company!",comp_id.id))
                if not int_acc_journal_id:
                    raise UserError(_("Please create an inter-company account journal for %s company!",comp_id.id))
                #Vendor Bill or Refund
                if move_type in ['in_invoice', 'in_refund']:
                    journal_lines.append((0, 0, {
                        'partner_id': inv_bill.partner_id.id,
                        'account_id': acc_pay_id.id,
                        'debit': intercompany_total_amount,
                        'credit': 0
                    }))
                    journal_lines.append((0, 0, {
                        'partner_id': inv_bill.partner_id.id,
                        'account_id': int_comp_acc_id.id,
                        'debit': 0,
                        'credit': intercompany_total_amount
                    }))
                if move_type in ['out_invoice','out_refund']:
                    journal_lines.append((0, 0, {
                        'partner_id': inv_bill.partner_id.id,
                        'account_id': acc_rec_id.id,
                        'debit': 0,
                        'credit': intercompany_total_amount
                    }))
                    journal_lines.append((0, 0, {
                        'partner_id': inv_bill.partner_id.id,
                        'account_id': int_comp_acc_id.id,
                        'debit': intercompany_total_amount,
                        'credit': 0
                    }))
                #Post the intercompany journal entry after creating it
                inter_comp_journal_entry = self.env['account.move'].create({
                        'partner_id': inv_bill.partner_id.id,
                        'date': self.payment_date,
                        'invoice_date_due': self.payment_date,
                        'company_id': comp_id.id,
                        'journal_id': int_acc_journal_id.id,
                        'move_type': 'entry',
                        'name': False,
                        'ref': intercompany_move_name,
                        'line_ids': journal_lines
                    })
                intercompany_journal_entries_list.append(inter_comp_journal_entry)
                inter_comp_journal_entry.action_post()
            #find the receivable or payable account for payment of particular selected company in wizard.
            dest_acc_rec_id = self.env['account.account'].search([('name','=','Account Receivable'),('company_id','=',self.company_id.id)], limit=1)
            dest_acc_pay_id = self.env['account.account'].search([('name','=','Account Payable'),('company_id','=',self.company_id.id)], limit=1)
            if not dest_acc_rec_id:
                raise UserError(_("Please create a receivable account for %s company!",self.company_id.id))
            if not dest_acc_pay_id:
                raise UserError(_("Please create a payable account for %s company!",self.company_id.id))
            # Update payment method for payments list for creating payments
            for pay_val in payment_vals_list:
                if pay_val['amount'] == inv_bill.total_amount and pay_val['payment_type'] == 'outbound':
                    pay_val.update({'payment_method_id': inv_bill.vendor_payment_method.id, 'destination_account_id': dest_acc_pay_id.id})
                    intercompany_journal_entries_ids_list = []
                    if intercompany_journal_entries_list:
                        for int_jrnl_entry in intercompany_journal_entries_list:
                            if any(int_move_nm in pay_val['ref'] for int_move_nm in intercompany_move_names_list):
                                intercompany_journal_entries_ids_list.append(int_jrnl_entry.id)
                            else:
                                continue
                    pay_val.update({'intercompany_move_ids': [(6,0,intercompany_journal_entries_ids_list)]})
                if pay_val['amount'] == inv_bill.total_amount and pay_val['payment_type'] == 'inbound':
                    pay_val.update({'payment_method_id': inv_bill.customer_payment_method.id, 'destination_account_id': dest_acc_rec_id.id})
                    intercompany_journal_entries_ids_list = []
                    if intercompany_journal_entries_list:
                        for int_jrnl_entry in intercompany_journal_entries_list:
                            if any(int_move_nm in pay_val['ref'] for int_move_nm in intercompany_move_names_list):
                                intercompany_journal_entries_ids_list.append(int_jrnl_entry.id)
                            else:
                                continue
                    pay_val.update({'intercompany_move_ids': [(6,0,intercompany_journal_entries_ids_list)]})
        payments = self.env['account.payment'].create(payment_vals_list)
        #update intercompany journal entry in related payment.
        # print ("intercompany_journal_entries_list --->>", intercompany_journal_entries_list)
        # if intercompany_journal_entries_list:
        #     for payment in payments:
        #         for int_jrnl_entry in intercompany_journal_entries_list:
        #             if int_jrnl_entry.ref in payment.ref:
        #                 payment.update({'intercompany_move_ids': [(6,0,[int_jrnl_entry.id])]})
        for int_je in intercompany_journal_entries_list:
            move_line_id = self.env['account.move.line'].search([('move_id','=',int_je.id)], limit=1)
            intercompany_moves_amount_total = 0.0
            for mv in intercompany_moves:
                for mv1 in mv:
                    intercompany_moves_amount_total += mv1.amount_total
                if int_je.amount_total == intercompany_moves_amount_total:
                    for mv2 in mv:
                        mv2.js_assign_outstanding_line(move_line_id.id)
                intercompany_moves.remove(mv)
        # If payments are made using a currency different than the source one, ensure the balance match exactly in
        # order to fully paid the source journal items.
        # For example, suppose a new currency B having a rate 100:1 regarding the company currency A.
        # If you try to pay 12.15A using 0.12B, the computed balance will be 12.00A for the payment instead of 12.15A.
        if edit_mode:
            for payment, lines in zip(payments, to_reconcile):
                # Batches are made using the same currency so making 'lines.currency_id' is ok.
                if payment.currency_id != lines.currency_id:
                    liquidity_lines, counterpart_lines, writeoff_lines = payment._seek_for_lines()
                    source_balance = abs(sum(lines.mapped('amount_residual')))
                    payment_rate = liquidity_lines[0].amount_currency / liquidity_lines[0].balance
                    source_balance_converted = abs(source_balance) * payment_rate

                    # Translate the balance into the payment currency is order to be able to compare them.
                    # In case in both have the same value (12.15 * 0.01 ~= 0.12 in our example), it means the user
                    # attempt to fully paid the source lines and then, we need to manually fix them to get a perfect
                    # match.
                    payment_balance = abs(sum(counterpart_lines.mapped('balance')))
                    payment_amount_currency = abs(sum(counterpart_lines.mapped('amount_currency')))
                    if not payment.currency_id.is_zero(source_balance_converted - payment_amount_currency):
                        continue

                    delta_balance = source_balance - payment_balance

                    # Balance are already the same.
                    if self.company_currency_id.is_zero(delta_balance):
                        continue

                    # Fix the balance but make sure to peek the liquidity and counterpart lines first.
                    debit_lines = (liquidity_lines + counterpart_lines).filtered('debit')
                    credit_lines = (liquidity_lines + counterpart_lines).filtered('credit')
                    
                    payment.move_id.write({'line_ids': [
                        (1, debit_lines[0].id, {'debit': debit_lines[0].debit + delta_balance}),
                        (1, credit_lines[0].id, {'credit': credit_lines[0].credit + delta_balance}),
                    ]})
        payments.action_post()
        domain = [('account_internal_type', 'in', ('receivable', 'payable')), ('reconciled', '=', False)]
        for payment, lines in zip(payments, to_reconcile):

            # When using the payment tokens, the payment could not be posted at this point (e.g. the transaction failed)
            # and then, we can't perform the reconciliation.
            if payment.state != 'posted':
                continue

            payment_lines = payment.line_ids.filtered_domain(domain)
            for account in payment_lines.account_id:
                (payment_lines + lines)\
                    .filtered_domain([('company_id','=',self.company_id.id),('reconciled', '=', False)])\
                    .reconcile()
                # (payment_lines + lines)\
                #     .filtered_domain([('account_id', '=', account.id), ('reconciled', '=', False)])\
                #     .reconcile()
        return payments
    
    
    @api.onchange('company_id')
    def _onchange_company_id(self):
        domain = [
                ('type', 'in', ('bank', 'cash')),
                ('company_id', '=', self.company_id.id),
            ]
        journal = None
        if self.source_currency_id:
            journal = self.env['account.journal'].search(domain + [('currency_id', '=', self.source_currency_id.id)])
        if not journal:
            journal = self.env['account.journal'].search(domain)
        if self.payment_difference and self.payment_difference > 0.0:
            self.payment_difference_handling = 'reconcile'
        if self.payment_difference and self.payment_difference_handling == 'reconcile':
            if self.company_id.discount_account_id:
                self.writeoff_account_id = self.company_id.discount_account_id.id
            else:
                self.writeoff_account_id = None
        res = {}
        res['domain'] = {'journal_id': [('id','in',journal.ids)]}
        return res
    
class UniqueInvoiceBill(models.TransientModel):
    
    _name = "unique.invoice.bills"
    _description = 'Unique Invoice/Bill'
    
    partner_id = fields.Many2one("res.partner")
    account_move_ids = fields.Many2many("account.move",'unique_invoice_bill_account_move_rel','unique_inv_bill_id','account_move_id', "Moves")
    company_id = fields.Many2one("res.company", string="Company")
    company_ids = fields.Many2many("res.company", 'res_company_unique_invoice_bills_rel','company_id','unique_invoice_bills_id', string="Companies")
    total_amount = fields.Float(string="Amount")
    customer_payment_method = fields.Many2one(related="partner_id.customer_payment_method", string="Customer Payment Method", readonly=False)
    vendor_payment_method = fields.Many2one(related="partner_id.vendor_payment_method", string="Vendor Payment Method", readonly=False)
