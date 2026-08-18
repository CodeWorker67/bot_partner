"""Тексты уведомлений по трафику Антиглушилка."""
from __future__ import annotations

from lexicon import lexicon

from wl_traffic.service import subscription_bonus_gb


def format_wl_bonus_suffix(duration_days: int) -> str:
    bonus = subscription_bonus_gb(duration_days)
    if bonus <= 0:
        return ""
    return lexicon["wl_bonus_line"].format(gb=bonus)


def format_pro_payment_link(duration_days: int) -> str:
    wl_bonus = format_wl_bonus_suffix(duration_days)
    return lexicon["payment_link"].format(wl_bonus=wl_bonus)


def format_wl_limit_exceeded(limit_gb: float, used_gb: float) -> str:
    return lexicon["wl_limit_exceeded"].format(
        limit_gb=limit_gb,
        used_gb=used_gb,
    )


def format_wl_traffic_low_warning(limit_gb: float, used_gb: float) -> str:
    return lexicon["wl_traffic_low_warning"].format(
        limit_gb=limit_gb,
        used_gb=used_gb,
    )


def format_wl_checker_exceeded_report(
    exceeded: list[tuple[int, float, float]],
) -> str:
    lines = [
        f"{user_id} - {used_gb:.2f} GB - {limit_gb:.2f} GB"
        for user_id, used_gb, limit_gb in exceeded
    ]
    return "📡 WL: превышение лимита\n\n" + "\n".join(lines)


def format_wl_checker_traffic_purchase(
    user_id: int,
    gb: int,
    used_gb: float,
    limit_gb: float,
) -> str:
    return (
        f"{user_id} купил трафик {gb} GB\n"
        f"Текущий расход - {used_gb:.2f} GB\n"
        f"Текущий лимит - {limit_gb:.2f} GB"
    )
