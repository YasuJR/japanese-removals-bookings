#!/usr/bin/env python3
"""Tests — persist Xero auth in PostgreSQL/integration_settings without deleting files."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-tests-only")

import database as db
from integrations import xero, xero_config


SAMPLE_TOKEN = {
    "access_token": "access-live-1",
    "refresh_token": "refresh-live-1",
    "token_type": "Bearer",
    "scope": "offline_access accounting.invoices accounting.payments",
    "expires_in": 1800,
}

SAMPLE_SETTINGS = {
    "client_id": "xero-client-id-live",
    "client_secret": "xero-client-secret-live",
    "tenant_id": "xero-tenant-live",
    "auto_create_on_booking_create": True,
}


def _init():
    db.init_db()


def _clear_db():
    db.save_integration_settings(xero_config.STORAGE_KEY, {})


def _write_live_files(tmp: Path) -> tuple[Path, Path]:
    settings_path = tmp / "xero_settings.json"
    token_path = tmp / "xero_token.json"
    settings_path.write_text(json.dumps(SAMPLE_SETTINGS, indent=2))
    token_path.write_text(json.dumps(SAMPLE_TOKEN, indent=2))
    return settings_path, token_path


def _patches(tmp: Path):
    settings_path, token_path = _write_live_files(tmp)
    return patch.multiple(
        xero_config,
        SETTINGS_PATH=settings_path,
        TOKEN_PATH=token_path,
    ), patch.object(xero_config, "_use_db_storage", return_value=True)


def test_migrates_files_into_db_without_deleting_them():
    _init()
    _clear_db()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        files, db_flag = _patches(tmp)
        with files, db_flag:
            stored = xero_config.read_stored_settings()
            db_row = db.get_integration_settings(xero_config.STORAGE_KEY)
            assert stored["client_id"] == SAMPLE_SETTINGS["client_id"]
            assert stored["client_secret"] == SAMPLE_SETTINGS["client_secret"]
            assert stored["tenant_id"] == SAMPLE_SETTINGS["tenant_id"]
            assert stored["token"]["refresh_token"] == "refresh-live-1"
            assert db_row["client_id"] == SAMPLE_SETTINGS["client_id"]
            assert db_row["token"]["access_token"] == "access-live-1"
            assert xero_config.SETTINGS_PATH.is_file()
            assert xero_config.TOKEN_PATH.is_file()
            assert json.loads(xero_config.TOKEN_PATH.read_text())["refresh_token"] == (
                "refresh-live-1"
            )
    return True


def test_loads_from_db_when_files_are_missing():
    _init()
    _clear_db()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        files, db_flag = _patches(tmp)
        with files, db_flag:
            xero_config.read_stored_settings()
            xero_config.SETTINGS_PATH.unlink()
            xero_config.TOKEN_PATH.unlink()
            stored = xero_config.read_stored_settings()
            assert stored["tenant_id"] == SAMPLE_SETTINGS["tenant_id"]
            assert stored["token"]["access_token"] == "access-live-1"
            assert xero.is_connected()
            assert xero.is_ready()
            xero_config.restore_files_from_stored()
            assert xero_config.SETTINGS_PATH.is_file()
            assert xero_config.TOKEN_PATH.is_file()
    return True


def test_token_refresh_writes_postgres_immediately():
    _init()
    _clear_db()
    refreshed = {
        "access_token": "access-live-2",
        "refresh_token": "refresh-live-2",
        "token_type": "Bearer",
        "scope": SAMPLE_TOKEN["scope"],
        "expires_in": 1800,
    }
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        files, db_flag = _patches(tmp)
        with files, db_flag:
            xero_config.read_stored_settings()
            with patch.object(xero, "_token_request", return_value=(True, refreshed, "")):
                assert xero._refresh_access_token() is True
            db_row = db.get_integration_settings(xero_config.STORAGE_KEY)
            assert db_row["token"]["access_token"] == "access-live-2"
            assert db_row["token"]["refresh_token"] == "refresh-live-2"
            assert json.loads(xero_config.TOKEN_PATH.read_text())["refresh_token"] == (
                "refresh-live-2"
            )
            assert db_row["client_id"] == SAMPLE_SETTINGS["client_id"]
    return True


def test_save_settings_preserves_token():
    _init()
    _clear_db()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        files, db_flag = _patches(tmp)
        with files, db_flag:
            xero_config.read_stored_settings()
            xero_config.save_settings(
                SAMPLE_SETTINGS["client_id"],
                "",
                "new-tenant-id",
            )
            stored = xero_config.read_stored_settings()
            assert stored["tenant_id"] == "new-tenant-id"
            assert stored["client_secret"] == SAMPLE_SETTINGS["client_secret"]
            assert stored["token"]["refresh_token"] == "refresh-live-1"
    return True


def test_db_token_wins_over_stale_env():
    _init()
    _clear_db()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        files, db_flag = _patches(tmp)
        with files, db_flag:
            xero_config.read_stored_settings()
            stale = json.dumps(
                {
                    "access_token": "stale-access",
                    "refresh_token": "stale-refresh",
                }
            )
            with patch.object(xero_config.config, "XERO_TOKEN_JSON", stale):
                stored = xero_config.read_stored_settings()
            assert stored["token"]["refresh_token"] == "refresh-live-1"
    return True


def test_empty_db_does_not_wipe_files():
    _init()
    _clear_db()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        files, db_flag = _patches(tmp)
        with files, db_flag:
            assert xero_config.SETTINGS_PATH.is_file()
            assert xero_config.TOKEN_PATH.is_file()
            stored = xero_config.read_stored_settings()
            assert stored["token"]["access_token"] == "access-live-1"
            assert json.loads(xero_config.SETTINGS_PATH.read_text())["client_id"] == (
                SAMPLE_SETTINGS["client_id"]
            )
    return True


def test_cron_bootstrap_loads_db_token():
    _init()
    _clear_db()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        files, db_flag = _patches(tmp)
        with files, db_flag:
            xero_config.read_stored_settings()
            xero_config.SETTINGS_PATH.unlink()
            xero_config.TOKEN_PATH.unlink()
            import production_bootstrap

            production_bootstrap.bootstrap_xero_settings()
            assert xero.is_ready()
            assert xero_config.TOKEN_PATH.is_file()
    return True


def main():
    tests = [
        test_migrates_files_into_db_without_deleting_them,
        test_loads_from_db_when_files_are_missing,
        test_token_refresh_writes_postgres_immediately,
        test_save_settings_preserves_token,
        test_db_token_wins_over_stale_env,
        test_empty_db_does_not_wipe_files,
        test_cron_bootstrap_loads_db_token,
    ]
    passed = 0
    for test in tests:
        try:
            if test():
                print("PASS:", test.__name__)
                passed += 1
            else:
                print("FAIL:", test.__name__)
        except Exception as exc:
            print("FAIL:", test.__name__, "—", exc)
    _clear_db()
    print("\n{0}/{1} passed".format(passed, len(tests)))
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
