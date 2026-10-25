"""Grafik Plotly untuk dashboard. Warna mengikuti laporan riset (teal = data utama)."""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

TEAL = "#0e6e66"
TEAL_SOFT = "rgba(14,110,102,0.28)"
AMBER = "#c27417"
RED = "#c0392b"
GREY = "#8c96a0"
COMMUNITY = ["#0e6e66", "#c27417", "#4a5fa8", "#8a4f9e", "#3c8d3c", "#b34a6a", "#5f7f8f", "#a08a2a"]
KIND_COLORS = {"laporan rutin": "#4a5fa8", "berita": TEAL, "ajakan/rekomendasi": RED, "lainnya": GREY}


def _layout(fig, height):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=28, b=8), hovermode="x unified",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return fig


def burst_color(n_channels):
    return RED if n_channels >= 3 else AMBER if n_channels == 2 else GREY


def weekly_messages(df):
    fig = go.Figure(go.Bar(x=df["minggu"], y=df["pesan"], marker_color=TEAL, name="Pesan"))
    fig.update_yaxes(title="pesan / minggu")
    return _layout(fig, 280)


def top_tickers(df, n=15):
    top = df.head(n).iloc[::-1]
    fig = go.Figure(go.Bar(x=top["mention"], y=top["ticker"], orientation="h", marker_color=TEAL,
                           customdata=np.stack([top["nama"], top["kanal"]], axis=-1),
                           hovertemplate="%{y} · %{customdata[0]}<br>%{x} pesan, %{customdata[1]} kanal<extra></extra>"))
    fig.update_layout(hovermode="closest")
    return _layout(fig, 26 * len(top) + 60)


def ticker_timeline(msgs, px, bursts, baseline_days=20):
    """Baris 1: pesan/hari + harga penutupan + penanda burst. Baris 2: volume + rata-rata N hari."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.62, 0.38], vertical_spacing=0.06,
                        specs=[[{"secondary_y": True}], [{}]])
    daily = msgs.groupby(msgs["waktu_wib"].dt.normalize()).size() if len(msgs) else pd.Series(dtype=int)
    fig.add_trace(go.Bar(x=daily.index, y=daily.values, name="Pesan/hari", marker_color=TEAL_SOFT,
                         marker_line_color=TEAL, marker_line_width=0.5), row=1, col=1)
    if len(px):
        fig.add_trace(go.Scatter(x=px["date"], y=px["close"], name="Harga penutupan", mode="lines",
                                 line=dict(color="#3b4652", width=1.6)), row=1, col=1, secondary_y=True)
        avg = px["volume"].rolling(baseline_days, min_periods=baseline_days).mean()
        fig.add_trace(go.Bar(x=px["date"], y=px["volume"], name="Volume", marker_color="rgba(59,70,82,0.45)"), row=2, col=1)
        fig.add_trace(go.Scatter(x=px["date"], y=avg, name=f"Rata-rata {baseline_days} hari", mode="lines",
                                 line=dict(color=AMBER, width=1.4, dash="dot")), row=2, col=1)
    if len(bursts):
        day = bursts["jam_wib"].dt.normalize()
        y = day.map(daily).fillna(0)
        ratio = bursts["volume_ratio_h1"].map(lambda v: f"{v:.2f}×" if pd.notna(v) else "–") \
            if "volume_ratio_h1" in bursts else "–"
        fig.add_trace(go.Scatter(
            x=day, y=y, mode="markers", name="Burst", marker=dict(symbol="triangle-up", size=12,
                                                                  color=[burst_color(n) for n in bursts["n_channels"]]),
            customdata=np.stack([bursts["jam_wib"].dt.strftime("%d %b %H.00 WIB"), bursts["count"],
                                 bursts["n_channels"], bursts["z"].round(1), ratio], axis=-1),
            hovertemplate="Burst %{customdata[0]}<br>%{customdata[1]} pesan, %{customdata[2]} kanal, z=%{customdata[3]}"
                          "<br>volume H+1 %{customdata[4]}<extra></extra>"), row=1, col=1)
    fig.update_yaxes(title_text="pesan", row=1, col=1, secondary_y=False, rangemode="tozero")
    fig.update_yaxes(title_text="harga (Rp)", row=1, col=1, secondary_y=True, showgrid=False)
    fig.update_yaxes(title_text="volume", row=2, col=1)
    return _layout(fig, 520)


def network(nodes, edges, label_top=14):
    fig = go.Figure()
    pos = {r.node: (r.x, r.y) for r in nodes.itertuples()}
    if len(edges):
        w = edges["norm_weight"]
        cuts = np.quantile(w, [0.5, 0.85]) if len(w) > 3 else [w.max() + 1, w.max() + 1]
        for lo, hi, width, alpha in [(-np.inf, cuts[0], 0.6, 0.25), (cuts[0], cuts[1], 1.6, 0.4), (cuts[1], np.inf, 3.4, 0.6)]:
            part = edges[(w > lo) & (w <= hi)]
            xs, ys = [], []
            for r in part.itertuples():
                if r.source in pos and r.target in pos:
                    xs += [pos[r.source][0], pos[r.target][0], None]
                    ys += [pos[r.source][1], pos[r.target][1], None]
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", hoverinfo="skip", showlegend=False,
                                     line=dict(width=width, color=f"rgba(110,120,130,{alpha})")))
    size = 8 + 26 * np.sqrt(nodes["pesan"].fillna(0) / max(nodes["pesan"].max() or 1, 1))
    top = set(nodes.sort_values("strength", ascending=False).head(label_top)["node"])
    colors = [GREY if ext else COMMUNITY[c % len(COMMUNITY)] if c >= 0 else GREY
              for c, ext in zip(nodes["community"], nodes["external"])]
    fig.add_trace(go.Scatter(
        x=nodes["x"], y=nodes["y"], mode="markers+text", showlegend=False,
        text=[n if n in top else "" for n in nodes["node"]], textposition="top center", textfont=dict(size=11),
        marker=dict(size=size, color=colors, symbol=["diamond" if e else "circle" for e in nodes["external"]],
                    line=dict(width=1, color="white")),
        customdata=np.stack([nodes["node"], nodes["pesan"].fillna(0).astype(int), nodes["community"]], axis=-1),
        hovertemplate="@%{customdata[0]}<br>%{customdata[1]} pesan · komunitas %{customdata[2]}<extra></extra>"))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False, scaleanchor="x")
    fig.update_layout(hovermode="closest")
    return _layout(fig, 620)


def channel_daily(msgs):
    daily = msgs.groupby(msgs["waktu_wib"].dt.normalize()).size()
    fig = go.Figure(go.Bar(x=daily.index, y=daily.values, marker_color=TEAL, name="Pesan/hari"))
    return _layout(fig, 260)


def kinds_bar(counts):
    total = counts.sum() or 1
    fig = go.Figure()
    for kind, color in KIND_COLORS.items():
        v = int(counts.get(kind, 0))
        fig.add_trace(go.Bar(y=[""], x=[v], orientation="h", name=kind, marker_color=color,
                             text=[f"{v / total:.0%}" if v / total > 0.07 else ""], textposition="inside",
                             hovertemplate=f"{kind}: {v} pesan<extra></extra>"))
    fig.update_layout(barmode="stack", hovermode="closest")
    fig.update_xaxes(visible=False)
    return _layout(fig, 120)
