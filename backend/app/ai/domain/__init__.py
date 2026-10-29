"""Pure AI domain: the placeholder grammar and the grounding validator.

Nothing here touches a database, a network or a clock. The validator is the
enforcement behind the product's central AI promise — *the model never invents a
number* — so it is written as pure data-in/data-out code that a corpus test can
exercise exhaustively without a model, a tenant or a session.
"""
