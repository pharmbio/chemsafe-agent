---
name: data_visualization
description: Produce publication-quality figures with matplotlib and seaborn — chart type selection, colorblind-safe palettes, panel arrangement, axis and legend conventions, sizing, DPI and export format, plus an automated figure-quality review. Use whenever the deliverable is a plot, chart, figure or other visual artifact, and consult it before generating the figure rather than after.
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

---

## Where the detail lives

- `read_files("references/figure-recipes.md")` — figure sizes, colorblind-safe
  palettes, multi-panel layout and panel labels, legends, axis formatting,
  plot-type recipes (scatter, box/violin, volcano, heatmap) and statistical
  annotation. Read this before building the figure.
- `read_files("references/size-cheatsheet.md")` — quick width/height lookup.

The protocols below are mandatory and apply to every figure.

### 10. Saving Figures — MANDATORY Protocol

```python
# ALWAYS save SVG (vector)
fig.savefig("somewhere/figure_name.svg", format="svg")
plt.close()
```

**Rules:**
- `format="svg"` for vector (editable in Illustrator/Inkscape)
- `plt.close()` after every save to free memory


---
### 11. Double check — MANDATORY Protocol
- Verify figure with figure_check tool.
- ALWAYS run `figure_check("path-to-figure.png")` after saving — fix any issues before proceeding

```python
from scripts.figure_check import figure_check

feedbacks = figure_check("path-to-figure.png")
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

