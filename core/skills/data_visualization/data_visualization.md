---
name: data visualizaiton
description: Use this skill whenever the task involves in generating figures or visualization. 
---


# Data Visualization Skills
Every figure must meet publication standards: minimal ink, maximum clarity, colorblind-safe, vector-exportable, and self-contained (readable without the main text).

---

## 1. Global Theme — Apply FIRST in every session

```python
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns
import numpy as np
import pandas as pd
from matplotlib.gridspec import GridSpec
from matplotlib.colors import LinearSegmentedColormap
import warnings
warnings.filterwarnings('ignore')

def set_publication_theme(
    font_family="Arial",
    base_font_size=7,
    figure_dpi=300,
    line_width=0.75,
):
    """Apply publication-quality theme globally."""
    matplotlib.rcParams.update({
        # Font
        "font.family": "sans-serif",
        "font.sans-serif": [font_family, "Helvetica", "DejaVu Sans"],
        "font.size": base_font_size,
        "axes.titlesize": base_font_size + 1,
        "axes.labelsize": base_font_size,
        "xtick.labelsize": base_font_size - 1,
        "ytick.labelsize": base_font_size - 1,
        "legend.fontsize": base_font_size - 1,
        "legend.title_fontsize": base_font_size,
        # Axes
        "axes.linewidth": line_width,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": False,
        # Ticks
        "xtick.major.width": line_width,
        "ytick.major.width": line_width,
        "xtick.minor.width": line_width * 0.6,
        "ytick.minor.width": line_width * 0.6,
        "xtick.major.size": 3,
        "ytick.major.size": 3,
        "xtick.direction": "out",
        "ytick.direction": "out",
        # Lines & markers
        "lines.linewidth": 1.0,
        "lines.markersize": 4,
        "patch.linewidth": line_width,
        # Figure
        "figure.dpi": figure_dpi,
        "savefig.dpi": figure_dpi,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.05,
        # Legend
        "legend.frameon": False,
        "legend.borderpad": 0.3,
        "legend.labelspacing": 0.3,
        "legend.handlelength": 1.2,
        "legend.handletextpad": 0.4,
        # Editable text in PDF/SVG
        "pdf.fonttype": 42,
        "svg.fonttype": "none",
        "ps.fonttype": 42,
    })

set_publication_theme()
```

**Key rules:**
- `font.size = 7` 
- `axes.spines.top/right = False` — always remove top/right spines
- `pdf.fonttype = 42` + `svg.fonttype = "none"` — keeps text editable in Illustrator/Inkscape
- `savefig.bbox = "tight"` — prevents clipping

---

## 2. Figure Sizes

```python
MM_TO_INCH = 1 / 25.4

def figsize(columns=1, height_mm=60):
    """
    Return (width, height) in inches for column widths.
    columns: 1 = 89 mm, 1.5 = 120 mm, 2 = 183 mm
    """
    widths = {1: 89, 1.5: 120, 2: 183}
    w_mm = widths.get(columns, columns * 89)
    return (w_mm * MM_TO_INCH, height_mm * MM_TO_INCH)

# Usage examples:
# Single panel:      fig, ax = plt.subplots(figsize=figsize(1, 60))
# Two-panel row:     fig, axes = plt.subplots(1, 2, figsize=figsize(2, 70))
# Complex layout:    fig = plt.figure(figsize=figsize(2, 120))
```

**Height guidelines:**
- Single scatter/bar: 55–70 mm
- Heatmap (12–20 genes): 70–100 mm
- Multi-panel figure: 100–160 mm

---

### 3. Color Palettes — Colorblind-Safe

```python
from matplotlib.colors import LinearSegmentedColormap

PALETTES = {
    # Categorical — Wong (2011) Nature Methods colorblind-safe palette
    "categorical_8": ["#0072B2","#E69F00","#009E73","#CC79A7",
                      "#56B4E9","#D55E00","#F0E442","#000000"],
    "categorical_6": ["#0072B2","#E69F00","#009E73","#CC79A7","#56B4E9","#D55E00"],
    "categorical_4": ["#0072B2","#E69F00","#009E73","#CC79A7"],
    "two_group":     ["#0072B2","#E69F00"],
    "three_group":   ["#0072B2","#E69F00","#009E73"],

    # Sequential (use for continuous single-variable data)
    "blues":   sns.color_palette("Blues", as_cmap=True),
    "reds":    sns.color_palette("Reds",  as_cmap=True),
    "viridis": matplotlib.cm.viridis,

    # Diverging (fold-change, correlation, z-score)
    "rdbu":     matplotlib.cm.RdBu_r,
    "coolwarm": matplotlib.cm.coolwarm,
    # Custom blue-white-red (publication standard for heatmaps)
    "bwr_pub": LinearSegmentedColormap.from_list(
        "bwr_pub", ["#2166AC","#F7F7F7","#D6604D"], N=256
    ),
}
```

**Rules:**
- ALWAYS use colorblind-safe palettes (Wong 2011 is the gold standard)
- Never use default matplotlib tab10 or jet
- Diverging colormaps: always center at 0 (use `vmin=-X, vmax=X`)
- For >8 groups: use shape + color encoding together

---

### 4. Multi-Panel Layout

```python
# Option A: Simple grid 
fig, axes = plt.subplots(2, 3, figsize=figsize(2, 120))
fig.subplots_adjust(hspace=0.55, wspace=0.45)

# Option B: Complex layout with GridSpec
fig = plt.figure(figsize=figsize(2, 120))
gs = GridSpec(2, 3, figure=fig, hspace=0.55, wspace=0.45)
ax_wide = fig.add_subplot(gs[1, 0:2])   # spans 2 columns
ax_tall = fig.add_subplot(gs[:, 2])     # spans 2 rows

# Option C: Nested GridSpec (insets, complex layouts) 
from matplotlib.gridspec import GridSpecFromSubplotSpec
gs_inner = GridSpecFromSubplotSpec(1, 2, subplot_spec=gs[0, 0], wspace=0.1)
```

**Spacing guidelines:**
- `hspace`: 0.45–0.6 (vertical gap between rows)
- `wspace`: 0.35–0.5 (horizontal gap between columns)
- Adjust per figure — always check for label overlap

---

### 5. Panel Labels (A, B, C…)

```python
import string

def add_panel_labels(axes, labels=None, x=-0.18, y=1.05,
                     fontsize=9, fontweight="bold"):
    """Add A, B, C… panel labels to a list of axes."""
    if labels is None:
        labels = list(string.ascii_uppercase)
    for ax, lbl in zip(axes, labels):
        ax.text(x, y, lbl, transform=ax.transAxes,
                fontsize=fontsize, fontweight=fontweight,
                va="top", ha="right")

# Usage:
add_panel_labels([ax1, ax2, ax3])
# Custom labels:
add_panel_labels([ax1, ax2], labels=["A", "B"], x=-0.20, y=1.08)
```

**Rules:**
- Bold, 8–10 pt (1–2 pt larger than body text)
- Position: upper-left corner of each panel (x≈-0.18, y≈1.05 in axes coords)
- Adjust x leftward if y-axis label is long

---

### 6. Legend Best Practices

```python
def clean_legend(ax, title=None, loc="best", ncol=1,
                 bbox_to_anchor=None, outside=False):
    """Frameless legend, optionally placed outside the axes."""
    kwargs = dict(title=title, loc=loc, ncol=ncol,
                  frameon=False, borderaxespad=0.3)
    if outside:
        kwargs.update(loc="upper left",
                      bbox_to_anchor=(1.02, 1),
                      borderaxespad=0)
    elif bbox_to_anchor:
        kwargs["bbox_to_anchor"] = bbox_to_anchor
    leg = ax.legend(**kwargs)
    if title and leg:
        leg.get_title().set_fontweight("bold")
    return leg

# Usage:
clean_legend(ax, title="Group")                    # inside, auto-position
clean_legend(ax, title="Group", outside=True)      # outside right
clean_legend(ax, ncol=2, loc="upper center",       # horizontal, top-center
             bbox_to_anchor=(0.5, 1.15))
```

**Rules:**
- NEVER use a box/frame around the legend (`frameon=False`)
- For ≤4 groups: inside the plot
- For ≥5 groups or dense plots: outside right (`outside=True`)
- For categorical scatter: use `ncol=2` if >4 items
- Legend title: bold, same size as body text

---

### 7. Axis Formatting

```python
def set_axis_style(ax, xlabel=None, ylabel=None, title=None,
                   xlim=None, ylim=None, xticks=None, yticks=None,
                   xticklabels=None, yticklabels=None,
                   rotate_xlabels=0):
    """One-call axis styling."""
    if xlabel:  ax.set_xlabel(xlabel, labelpad=4)
    if ylabel:  ax.set_ylabel(ylabel, labelpad=4)
    if title:   ax.set_title(title, pad=4)
    if xlim:    ax.set_xlim(xlim)
    if ylim:    ax.set_ylim(ylim)
    if xticks is not None:  ax.set_xticks(xticks)
    if yticks is not None:  ax.set_yticks(yticks)
    if xticklabels is not None:
        ax.set_xticklabels(xticklabels,
                           rotation=rotate_xlabels,
                           ha="right" if rotate_xlabels else "center")
    if yticklabels is not None:
        ax.set_yticklabels(yticklabels)

def despine_minimal(ax, keep=("left","bottom")):
    """Keep only specified spines (e.g., for violin/box plots)."""
    for spine in ["top","right","left","bottom"]:
        ax.spines[spine].set_visible(spine in keep)
```

**Rules:**
- Axis labels: concise, include units in parentheses — e.g., `"Expression (TPM)"`, `"log₂ fold change"`
- Tick labels: rotate 30–45° only when necessary (long category names)
- Always set `labelpad=4` to prevent label-tick overlap
- For log axes: use `ax.set_xscale("log")` + `ax.xaxis.set_major_formatter(matplotlib.ticker.ScalarFormatter())`

---

### 8. Plot-Type Recipes

#### Scatter / PCA / UMAP
```python
ax.scatter(x, y, c=color, s=12, alpha=0.75, linewidths=0, rasterized=True)
# rasterized=True is CRITICAL for large datasets (>1000 points) — keeps SVG file small
```

#### Bar plot with error bars
```python
ax.bar(x_pos, means, yerr=sems, color=colors, width=0.6,
       capsize=3, error_kw={"linewidth": 0.75}, linewidth=0)
ax.axhline(baseline, color="black", linewidth=0.6, linestyle="--", alpha=0.5)
```

#### Box + strip (jitter) — preferred over bar for n<30
```python
bp = ax.boxplot(data_list, patch_artist=True, widths=0.5,
                medianprops={"color":"black","linewidth":1.2},
                whiskerprops={"linewidth":0.75},
                capprops={"linewidth":0.75},
                flierprops={"marker":"o","markersize":2,"alpha":0.4})
for patch, col in zip(bp["boxes"], colors):
    patch.set_facecolor(col); patch.set_alpha(0.7); patch.set_linewidth(0.75)
# Overlay jittered points
for i, (d, col) in enumerate(zip(data_list, colors)):
    jitter = np.random.uniform(-0.15, 0.15, len(d))
    ax.scatter(np.full(len(d), i+1) + jitter, d,
               color=col, s=6, alpha=0.5, zorder=3, linewidths=0)
```

#### Heatmap
```python
im = ax.imshow(matrix, aspect="auto", cmap=PALETTES["bwr_pub"],
               vmin=-3, vmax=3, interpolation="nearest")
ax.spines[:].set_visible(False)  # remove all spines for heatmaps
cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02, shrink=0.8, aspect=20)
cbar.set_label("z-score", fontsize=6)
cbar.ax.tick_params(labelsize=5, length=2)
cbar.outline.set_linewidth(0.5)
```

#### Volcano plot
```python
# Layer order: NS (gray) → significant points → threshold lines
ax.scatter(lfc[ns],     neglog_p[ns],     c="#AAAAAA", s=6, alpha=0.4,
           linewidths=0, rasterized=True, label="NS")
ax.scatter(lfc[sig_up], neglog_p[sig_up], c=colors[0], s=8, alpha=0.8,
           linewidths=0, rasterized=True, label=f"Up (n={sig_up.sum()})")
ax.scatter(lfc[sig_dn], neglog_p[sig_dn], c=colors[1], s=8, alpha=0.8,
           linewidths=0, rasterized=True, label=f"Down (n={sig_dn.sum()})")
ax.axhline(-np.log10(0.05), color="black", linewidth=0.6, linestyle="--", alpha=0.6)
ax.axvline(1.0,  color="black", linewidth=0.6, linestyle="--", alpha=0.4)
ax.axvline(-1.0, color="black", linewidth=0.6, linestyle="--", alpha=0.4)
# Annotate threshold
ax.text(ax.get_xlim()[1]*0.98, -np.log10(0.05)+0.1, "p=0.05",
        ha="right", va="bottom", fontsize=5, color="gray")
```

#### Line plot (time series / dose-response)
```python
for i, (grp, col) in enumerate(zip(groups, colors)):
    ax.plot(x, y[i], color=col, linewidth=1.2, label=grp)
    ax.fill_between(x, y[i]-sem[i], y[i]+sem[i], color=col, alpha=0.15)
```

---

### 9. Statistical Annotations

```python
def add_significance_bracket(ax, x1, x2, y, h, p_value,
                              fontsize=6, linewidth=0.75):
    """Draw a significance bracket between two groups."""
    if p_value < 0.001:   label = "***"
    elif p_value < 0.01:  label = "**"
    elif p_value < 0.05:  label = "*"
    else:                 label = "ns"
    ax.plot([x1, x1, x2, x2], [y, y+h, y+h, y],
            lw=linewidth, color="black")
    ax.text((x1+x2)/2, y+h, label, ha="center", va="bottom",
            fontsize=fontsize)
```

---

### 10. Saving Figures — MANDATORY Protocol

```python
# ALWAYS save SVG (vector)
fig.savefig("/mnt/results/figure_name.svg", format="svg")
plt.close()
```

**Rules:**
- `format="svg"` for vector (editable in Illustrator/Inkscape)
- `plt.close()` after every save to free memory


---
### 11. Double check — MANDATORY Protocol
- Verify figure with figure_check tool.
- ALWAYS run `Rigure_check("path-to-figure.png")` after saving — fix any issues before proceeding

```python
from core.tools import figure_check

result = figure_check("path-to-figure.png")
```


### 12. Common Mistakes to Avoid

| ❌ Wrong | ✅ Correct |
|----------|-----------|
| Default matplotlib colors | Wong 2011 colorblind-safe palette |
| `legend(frameon=True)` | `legend(frameon=False)` |
| Top/right spines visible | `axes.spines.top/right = False` |
| `fontsize=12` in small panels | `fontsize=7` |
| `plt.tight_layout()` on complex grids | Manual `hspace`/`wspace` in GridSpec |
| Saving only PNG | Save SVG + PNG both |
| No panel labels | Bold A, B, C… at upper-left |
| Bar plots for small n | Box+strip for n<30 |
| `rasterized=False` on scatter >1000 pts | `rasterized=True` |
| Jet/rainbow colormap | Viridis/RdBu_r/bwr_pub |
| Axis label without units | Include units: `"Expression (TPM)"` |
| No colorbar label | Always label colorbars |
| Overlapping tick labels | Rotate 30–45° or reduce tick density |

---

### 13. Quick Reference — Figure Size Cheat Sheet

| Layout | `figsize()` call | height_mm |
|--------|------------------------|-----------|
| Single panel | `figsize(1, 65)` | 65 |
| 2-panel row | `figsize(2, 70)` | 70 |
| 3-panel row | `figsize(2, 65)` | 65 |
| 2×2 grid | `figsize(2, 130)` | 130 |
| 2×3 grid | `figsize(2, 120)` | 120 |
| Tall heatmap | `figsize(1, 110)` | 110 |
