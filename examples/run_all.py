"""
Запуск всех цепочек запросов и сводный отчёт.

Использование:
    python examples/run_all.py [--base-url http://127.0.0.1:8080] [--filter positive]

Параметры:
    --base-url  URL mock-сервера (default: http://127.0.0.1:8080)
    --filter    Фильтр: positive | negative | flk | all (default: all)
    --stop-on-fail  Остановиться при первой ошибке
"""

import sys
import os
import argparse
import importlib
import traceback
import time
import io

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ------------------------------------------------------------------ #
#  Registry                                                            #
# ------------------------------------------------------------------ #

CHAINS = [
    # group, module_path, label
    ("positive", "examples.positive.pos_001_full_flow",    "POS-001 Полный флоу аренды"),
    ("positive", "examples.positive.pos_008_payment_retry","POS-008 Retry платежа"),
    ("positive", "examples.positive.pos_009_user_cancel",  "POS-009 Отмена пользователем"),
    ("positive", "examples.positive.pos_010_auto_expire",  "POS-010 Авто-истечение бронирования"),
    ("positive", "examples.positive.pos_013_fine",         "POS-013 Штраф после аренды"),
    ("positive", "examples.positive.pos_014_damage",       "POS-014 Damage report"),
    ("positive", "examples.positive.pos_015_refund",       "POS-015 Возврат средств"),
    ("negative", "examples.negative.neg_002_expired_booking", "NEG-002 Истёкшее бронирование"),
    ("negative", "examples.negative.neg_003_expired_license", "NEG-003 Просроченное ВУ"),
    ("negative", "examples.negative.neg_004_blocked_user",    "NEG-004 Заблокированный пользователь"),
    ("negative", "examples.negative.neg_005_double_booking",  "NEG-005 Двойное бронирование"),
    ("negative", "examples.negative.neg_006_outside_zone",    "NEG-006 Вне разрешённой зоны"),
    ("negative", "examples.negative.neg_007_payment_error",   "NEG-007 Ошибка провайдера"),
    ("negative", "examples.negative.neg_011_telemetry_lost",  "NEG-011 Потеря телеметрии"),
    ("negative", "examples.negative.neg_012_idempotency",     "NEG-012 Идемпотентность"),
    ("flk",     "examples.flk.flk_all_rules",              "FLK Все правила ФЛК"),
]


# ------------------------------------------------------------------ #
#  Runner                                                              #
# ------------------------------------------------------------------ #

def run_chain(module_path: str, label: str, base_url: str) -> tuple[bool, float, str]:
    try:
        mod = importlib.import_module(module_path)
        t0 = time.time()
        result = mod.run(base_url)
        elapsed = time.time() - t0
        return result, elapsed, ""
    except Exception as e:
        return False, 0.0, traceback.format_exc()


def main():
    parser = argparse.ArgumentParser(description="Run all carsharing mock chains")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--filter", choices=["positive", "negative", "flk", "all"], default="all")
    parser.add_argument("--stop-on-fail", action="store_true")
    args = parser.parse_args()

    chains = CHAINS if args.filter == "all" else [c for c in CHAINS if c[0] == args.filter]

    # Check server availability
    try:
        import urllib.request
        urllib.request.urlopen(f"{args.base_url}/health", timeout=5)
    except Exception as e:
        print(f"\n[ERROR] Mock server не доступен: {args.base_url}/health\n  {e}")
        print("  Запустите: python run_mock_server.py")
        sys.exit(1)

    print(f"\n{'='*65}")
    print(f"  Carsharing Mock - Request Chains Runner")
    print(f"  Server: {args.base_url}")
    print(f"  Filter: {args.filter}  |  Chains: {len(chains)}")
    print(f"{'='*65}\n")

    results = []
    passed = 0
    failed = 0

    groups_seen = set()
    for group, module_path, label in chains:
        if group not in groups_seen:
            print(f"-- {group.upper()} {'-'*50}")
            groups_seen.add(group)

        print(f"  Running: {label}")
        ok, elapsed, err = run_chain(module_path, label, args.base_url)

        if ok:
            passed += 1
        else:
            failed += 1
            if err:
                print(f"    [EXCEPTION]\n{err}")

        results.append((group, label, ok, elapsed))

        if not ok and args.stop_on_fail:
            print(f"\n  [STOP] Stopped on first failure: {label}")
            break

    # ---- Summary ----
    print(f"\n{'='*65}")
    print(f"  RESULTS: {passed} passed / {failed} failed / {len(results)} total")
    print(f"{'='*65}")

    for group, label, ok, elapsed in results:
        icon = "+" if ok else "x"
        ms = f"{elapsed*1000:.0f}ms"
        print(f"  {icon}  [{group:8s}] {label:45s} {ms:>7}")

    print()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
