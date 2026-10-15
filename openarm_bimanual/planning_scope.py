"""Include each current physical holder when predicting object collision sweeps."""
def planning_sides(requested, holders):
    sides = {s for s in ['left', 'right'] if s in requested or s + '_gripper' in requested}
    sides.update(holders)
    if not sides <= {'left', 'right'}: raise ValueError('Invalid physical holder')
    return sorted(sides or {'left'})


def physical_holders(contacts, holds, receipts, epoch):
    """V2 planning attachments require current certificates, never bare contact."""
    return [s for s in ['left','right'] if s in holds and contacts[s].get('hold_valid') is True
            and (receipts.get(s) or {}).get('schema') == 'openarm.grasp/2'
            and receipts[s].get('epoch') == epoch and receipts[s].get('holding') is True]
