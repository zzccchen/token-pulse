from token_pulse.preferences import Preferences, load, save


def test_preferences_are_typed_and_atomic(tmp_path):
    path = tmp_path / "settings.json"
    assert load(path) == Preferences()
    value = Preferences("test directory", True, False)
    save(path, value)
    assert load(path) == value
    assert list(tmp_path.glob("*.tmp")) == []
    path.write_text('{"source":42,"tray_number":"yes"}')
    assert load(path).source == ""
    assert not load(path).tray_number
    path.write_text("[]")
    assert load(path) == Preferences()


def test_appearance_round_trips_and_old_settings_have_readable_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"source":"demo"}')
    assert load(path).appearance == "light"
    assert not load(path).reduce_transparency
    assert not load(path).reduce_motion
    preferences = Preferences(appearance="dark", reduce_transparency=True, reduce_motion=True)
    save(path, preferences)
    assert load(path) == preferences


def test_percentile_filter_defaults_on_and_persists_explicit_off(tmp_path):
    path = tmp_path / "settings.json"
    for content in ("{}", '{"trim_extremes":null}', '{"trim_extremes":"false"}'):
        path.write_text(content)
        assert load(path).trim_extremes
    for enabled in (False, True):
        preferences = Preferences(trim_extremes=enabled)
        save(path, preferences)
        assert load(path) == preferences
