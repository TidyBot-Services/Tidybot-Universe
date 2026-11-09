"""Offline tests for the R5 success rule v2 (fixture_success.py); the teleport check in
~/下载/success-teleport-20261110 exercises the same rule on real scenes."""
from types import SimpleNamespace as NS

import numpy as np

from openarm365_dev_v2 import fixture_success as rule


class FakeEnv:
    """Native-right gripper = physical left (OpenArm mapping); success = native-right site far."""

    def __init__(self, name='TurnOnMicrowave'):
        self.__class__ = type(name, (FakeEnv,), {})
        self.right, self.left = NS(tag='native_right'), NS(tag='native_left')
        self.robots = [NS(gripper={'right': self.right, 'left': self.left}, eef_site_id={'right': 0, 'left': 1})]
        self.site_far = {0: True, 1: True}
        self.contacts = set()
        self.turned_on = False
        self.resets = 0

    def check_contact(self, a, b=None):
        return (a.tag, b) in self.contacts

    def update_state(self):
        if self.check_contact(self.robots[0].gripper['right'], 'start_button'):
            self.turned_on = True

    def reset(self):
        self.resets += 1

    def _check_success(self):
        return self.turned_on and self.site_far[self.robots[0].eef_site_id['right']]


def test_physical_right_press_counts_after_install():
    env = FakeEnv()
    env.contacts.add(('native_left', 'start_button'))
    env.update_state()
    assert not env.turned_on  # upstream: physical right press ignored
    rule.install(env)
    env.update_state()
    assert env.turned_on and rule.evaluate(env)['native_success'] is True


def test_both_grippers_must_be_released_and_site_ids_restored():
    for held_site in (0, 1):
        env = rule.install(FakeEnv())
        env.turned_on = True
        env.site_far[held_site] = False
        v = rule.evaluate(env)
        assert v['native_success'] is False
        assert env.robots[0].eef_site_id == {'right': 0, 'left': 1}
    env = rule.install(FakeEnv()); env.turned_on = True
    v = rule.evaluate(env)
    assert v == dict(native_success=True, success_rule=rule.RULE, upstream_native_success=True,
                     upstream_with_physical_right_gripper=True)


def test_install_is_idempotent_and_reset_clears_monitor():
    env = rule.install(FakeEnv())
    first = env.check_contact
    assert rule.install(env).check_contact is first
    env._attentionbench_monitor['x'] = 1
    env.reset()
    assert env.resets == 1 and env._attentionbench_monitor == {}


def test_prepare_coffee_order(monkeypatch):
    env = FakeEnv('PrepareCoffee')
    env.coffee_machine = NS(_turned_on=False)
    mug = {'at': False}
    monkeypatch.setattr(rule, '_mug_at_outlet', lambda e: mug['at'])
    monkeypatch.setattr(rule, '_upright', lambda e, n: True)
    rule.install(env)
    original = env.update_state

    def press():
        env.coffee_machine._turned_on = True
        rule._monitor_step(env, {'coffee_on': False})
    press()  # pressed before the mug arrived
    mug['at'] = True
    env.turned_on = True
    assert rule.evaluate(env)['native_success'] is False
    env._attentionbench_monitor.clear(); env.coffee_machine._turned_on = False
    press()  # mug already there
    assert rule.evaluate(env)['native_success'] is True
    assert original is env.update_state


def test_kebab_door_and_timer(monkeypatch):
    env = FakeEnv('HeatKebabSandwich')
    state = {'door': 0.0, 'time': 0.0, 'food': True}
    env.toaster_oven = NS()
    monkeypatch.setattr(rule, '_door_fraction', lambda e, o: state['door'])
    monkeypatch.setattr(rule, '_time_value', lambda e, o: state['time'])
    monkeypatch.setattr(rule, '_kebab_foods_in', lambda e, o: state['food'])
    rule.install(env)
    env.turned_on = True

    def turn(t):
        state['time'] = t
        rule._monitor_step(env, {})
    state['door'] = 0.5; turn(0.5)
    assert rule.evaluate(env)['native_success'] is False   # door half open
    state['door'] = 0.0
    assert rule.evaluate(env)['native_success'] is False   # timer was turned while open
    turn(0.6)
    assert rule.evaluate(env)['native_success'] is True
    state['door'] = 0.011
    assert rule.evaluate(env)['native_success'] is False   # above the 0.01 snap-shut threshold
    assert np.isclose(rule.KEBAB_DOOR_CLOSED, 0.01)
