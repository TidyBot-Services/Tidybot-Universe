"""Direct startup cannot bypass service identity authentication."""
from curobo_v2.identity import IdentityError
raise IdentityError("Use curobo_v2.session.V2Service with an explicit lock and robot configuration")
