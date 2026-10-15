"""Mechanical collision semantics derived only from the locked URDF topology."""
from collections import defaultdict


def fixed_body_components(xml):
    """Links joined exclusively by fixed joints form one rigid collision body.

    A loader can keep these as separate entities, including shape-less intermediate
    links. Their internal mesh overlaps are not self collisions. This never exempts
    a movable-joint pair or any robot/world pair, and reads no editable ACM list.
    """
    parent = {node.get('name'): node.get('name') for node in xml.findall('link')}
    def find(name):
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name
    for joint in xml.findall('joint'):
        if joint.get('type') == 'fixed':
            a = joint.find('parent').get('link')
            b = joint.find('child').get('link')
            parent[find(b)] = find(a)
    components = defaultdict(list)
    for name in parent:
        components[find(name)].append(name)
    return {name: find(name) for name in parent}, [sorted(names) for names in components.values() if len(names) > 1]


def same_fixed_body(names, groups):
    return len(names) == 2 and names[0] in groups and names[1] in groups and groups[names[0]] == groups[names[1]]
