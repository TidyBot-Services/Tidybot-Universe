"""Each native view retains the original bounded two-GB stream allowance."""
VIEW_LIMIT_BYTES = 2_000_000_000
VIEWS = {'chest', 'external', 'left_wrist', 'right_wrist'}


def enforce_storage_budget(bytes_by_view):
    if (not isinstance(bytes_by_view, dict) or not set(bytes_by_view) <= VIEWS
            or any(type(n) is not int or n < 0 for n in bytes_by_view.values())):
        raise ValueError('Invalid display storage accounting')
    if any(n > VIEW_LIMIT_BYTES for n in bytes_by_view.values()):
        raise RuntimeError('Display recording storage limit reached for one native view')
