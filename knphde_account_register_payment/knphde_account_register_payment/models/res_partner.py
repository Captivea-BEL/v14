

from odoo import models, fields, api


class ResPartner(models.Model):
    
    _inherit = "res.partner"
    
    customer_payment_method = fields.Many2one("account.payment.method",string="Customer Payment Method", domain=[('payment_type','=','inbound')])
    vendor_payment_method = fields.Many2one("account.payment.method", string="Vendor Payment Method", domain=[('payment_type','=','outbound')])


class ResCompany(models.Model):

    _inherit = "res.company"

    discount_account_id = fields.Many2one("account.account", string="Discount Account", domain=lambda self: "[('company_id.id', '=', id)]")
