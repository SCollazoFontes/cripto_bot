from __future__ import annotations

# -----------------------------------------------------------------------------
# Métricas de Performance para Trading (Sharpe, Sortino, MaxDD, etc.)
# -----------------------------------------------------------------------------


def calculate_returns(equity_curve: list[float]) -> list[float]:
    """Calcula retornos porcentuales entre barras."""
    if len(equity_curve) < 2:
        return []
    returns: list[float] = []
    for i in range(1, len(equity_curve)):
        if equity_curve[i - 1] == 0:
            returns.append(0.0)
        else:
            ret = (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            returns.append(ret)
    return returns


def calculate_sharpe(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """
    Sharpe Ratio = (Retorno promedio - Tasa libre riesgo) / Desviación estándar

    Interpretación:
    - > 1.0: Bueno
    - > 2.0: Muy bueno
    - > 3.0: Excelente
    """
    if not returns or len(returns) < 2:
        return 0.0
    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
    std_return = variance**0.5
    if std_return == 0:
        return 0.0
    return (mean_return - risk_free_rate) / std_return


def calculate_sortino(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """
    Sortino Ratio = (Retorno - RF) / Downside Deviation
    Similar a Sharpe pero solo penaliza volatilidad negativa.

    Interpretación: Similar a Sharpe, pero más generoso
    """
    if not returns or len(returns) < 2:
        return 0.0
    mean_return = sum(returns) / len(returns)
    downside_returns = [r for r in returns if r < 0]
    if not downside_returns:
        return float("inf") if mean_return > 0 else 0.0
    downside_mean = sum(downside_returns) / len(downside_returns)
    # Use population variance to avoid zero-division when only one downside return
    downside_variance = sum((r - downside_mean) ** 2 for r in downside_returns) / len(
        downside_returns
    )
    downside_std = downside_variance**0.5
    if downside_std == 0:
        return 0.0
    return (mean_return - risk_free_rate) / downside_std


def calculate_max_drawdown(equity_curve: list[float]) -> tuple[float, int, int]:
    """
    Maximum Drawdown = Máxima pérdida desde peak hasta trough.

    Returns:
        (max_dd, peak_idx, trough_idx) en formato (%, index, index)
    """
    if not equity_curve or len(equity_curve) < 2:
        return 0.0, 0, 0

    peak = equity_curve[0]
    peak_idx = 0
    max_dd = 0.0
    max_dd_peak_idx = 0
    max_dd_trough_idx = 0

    for i, value in enumerate(equity_curve):
        if value > peak:
            peak = value
            peak_idx = i
        dd = (peak - value) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            max_dd_peak_idx = peak_idx
            max_dd_trough_idx = i

    return max_dd, max_dd_peak_idx, max_dd_trough_idx


def calculate_profit_factor(trades_pnl: list[float]) -> float:
    """
    Profit Factor = Gross Profit / Gross Loss

    Interpretación:
    - > 1.0: Estrategia rentable
    - > 1.5: Buena
    - > 2.0: Excelente
    """
    winning_trades = [pnl for pnl in trades_pnl if pnl > 0]
    losing_trades = [pnl for pnl in trades_pnl if pnl < 0]

    gross_profit = sum(winning_trades) if winning_trades else 0.0
    gross_loss = abs(sum(losing_trades)) if losing_trades else 0.0

    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def calculate_win_rate(trades_pnl: list[float]) -> tuple[float, int, int]:
    """
    Win Rate = Winning Trades / Total Trades

    Returns:
        (win_rate, num_wins, num_losses)
    """
    if not trades_pnl:
        return 0.0, 0, 0

    num_wins = sum(1 for pnl in trades_pnl if pnl > 0)
    num_losses = sum(1 for pnl in trades_pnl if pnl < 0)
    total_trades = len(trades_pnl)

    win_rate = num_wins / total_trades if total_trades > 0 else 0.0
    return win_rate, num_wins, num_losses


def calculate_avg_win_loss(trades_pnl: list[float]) -> tuple[float, float]:
    """
    Calcula ganancia promedio y pérdida promedio.

    Returns:
        (avg_win, avg_loss)
    """
    winning_trades = [pnl for pnl in trades_pnl if pnl > 0]
    losing_trades = [pnl for pnl in trades_pnl if pnl < 0]

    avg_win = sum(winning_trades) / len(winning_trades) if winning_trades else 0.0
    avg_loss = sum(losing_trades) / len(losing_trades) if losing_trades else 0.0

    return avg_win, avg_loss


def calculate_all_metrics(
    equity_curve: list[float], trades_pnl: list[float], risk_free_rate: float = 0.0
) -> dict:
    """
    Calcula todas las métricas de una vez.

    Args:
        equity_curve: Lista de valores de equity por barra
        trades_pnl: Lista de PnL por trade (positivo o negativo)
        risk_free_rate: Tasa libre de riesgo (default 0)

    Returns:
        Dict con todas las métricas calculadas
    """
    returns = calculate_returns(equity_curve)
    max_dd, dd_peak_idx, dd_trough_idx = calculate_max_drawdown(equity_curve)
    win_rate, num_wins, num_losses = calculate_win_rate(trades_pnl)
    avg_win, avg_loss = calculate_avg_win_loss(trades_pnl)

    return {
        "sharpe_ratio": calculate_sharpe(returns, risk_free_rate),
        "sortino_ratio": calculate_sortino(returns, risk_free_rate),
        "max_drawdown": max_dd,
        "max_drawdown_peak_idx": dd_peak_idx,
        "max_drawdown_trough_idx": dd_trough_idx,
        "profit_factor": calculate_profit_factor(trades_pnl),
        "win_rate": win_rate,
        "num_winning_trades": num_wins,
        "num_losing_trades": num_losses,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_trade": (sum(trades_pnl) / len(trades_pnl)) if trades_pnl else 0.0,
        "total_return": (
            (equity_curve[-1] - equity_curve[0]) / equity_curve[0]
            if equity_curve and equity_curve[0] > 0
            else 0.0
        ),
    }
