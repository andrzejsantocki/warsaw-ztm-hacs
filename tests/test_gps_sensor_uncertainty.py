"""GPS candidate uncertainty survives the real HA sensor adapter."""
from types import SimpleNamespace
import pytest
from custom_components.warsaw_transit.sensor import TransitSensor


@pytest.mark.parametrize('gps', [None, {'vehicle_id': 'V', 'certainty': 'estimated_schedule_and_motion', 'warning': 'Direction is not guaranteed without a trip feed'}])
def test_sensor_exposes_candidate_or_unknown_uncertainty(gps):
    reason = 'direction_estimated_without_trip_id' if gps else 'no_unambiguous_fresh_schedule_and_motion_candidate'
    coordinator = SimpleNamespace(data={'a': {'gps': gps, 'status': 'approaching_candidate' if gps else 'unknown', 'uncertainty_reason': reason}}, last_update_success=True)
    sensor = TransitSensor(coordinator, 'entry', 'a', 'approaching_bus')
    assert sensor.extra_state_attributes.get('uncertainty_reason') == reason
    assert 'gps' not in sensor.extra_state_attributes
    if gps:
        assert sensor.native_value == 'V'
        assert sensor.extra_state_attributes['certainty'] == 'estimated_schedule_and_motion'
    coordinator.last_update_success = False
    assert sensor.extra_state_attributes['uncertainty_reason'] == 'update_failed'
    assert 'vehicle_id' not in sensor.extra_state_attributes
