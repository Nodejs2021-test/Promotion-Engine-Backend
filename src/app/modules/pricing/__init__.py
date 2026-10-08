"""Pricing: the campaign rules engine and the pricing API.

The engine files are pure and deterministic (no database, no clock); service.py loads the approved campaigns and
passes them in, so the same input always gives the same result.

    router.py       POST /pricing/evaluate
    schema.py       the pricing request
    service.py      prices a sales order with today's approved campaigns

    model.py        the inputs (campaign, rule, order) and the vocabulary (fields, operators, actions)
    eligibility.py  campaign date, customer eligibility and item eligibility checks
    conditions.py   rule conditions (customer, product, quantity, order value)
    priority.py     the order in which rules are checked
    discount.py     discount / promotional price calculation
    engine.py       evaluate_sales_order(): checks the rules in order, the first matching rule is applied
"""
