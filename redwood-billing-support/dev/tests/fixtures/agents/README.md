# Fixture agents

`always_escalate/` is a workspace layer that hands every conversation off to Billing Ops without calling a model.
`test_grade.py` runs it through the real conversation loop and checks that the console records the escalated
case and the conversation ends in a transfer.
