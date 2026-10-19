"""OnCallify: an on-call assistant that is measured, not trusted.

Design rules (credit: the safety design follows Beacon, see docs/ORIGIN.md):

* The model never decides what is allowed. An allowlist in code does.
* Approval is a stored record bound to one action and its exact parameters
  (a hash), used once, with an expiry. Not a prompt.
* "Recovered" is a claim that must be proven by metrics after the fix, not read
  from the model.
* Every diagnoser variant is evaluated on the same replayed incidents before it
  is trusted with the next real one.
"""

__version__ = "1.0.0"
