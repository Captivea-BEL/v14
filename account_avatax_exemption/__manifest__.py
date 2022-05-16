{
    "name": "Avatax Exemptions",
    "version": "14.0.1.1.0",
    "category": "Sales",
    "summary": """
        This application allows you to add exemptions to Avatax
    """,
    "website": "https://github.com/OCA/account-fiscal-rule",
    "author": "Sodexis, Odoo Community Association (OCA)",
    "license": "AGPL-3",
    "depends": ["product", "queue_job", "account_avatax_exemption_base"],
    "data": [
        "security/ir.model.access.csv",
        "data/cron.xml",
        "data/queue.xml",
        "data/ir_sequence_data.xml",
        "views/avalara_salestax_view.xml",
        "views/avalara_exemption_view.xml",
        "views/product_view.xml",
        "views/exemption_template_views.xml",
        "views/res_country_state_view.xml",
    ],
    "external_dependencies": {"python": ["Avalara"]},
    "installable": True,
    "application": True,
}
