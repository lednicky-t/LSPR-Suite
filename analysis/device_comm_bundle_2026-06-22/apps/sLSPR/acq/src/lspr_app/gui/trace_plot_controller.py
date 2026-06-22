from __future__ import annotations

from lspr_app.gui.plot_controller import (
    autoscale_metric_plot,
    handle_metric_mouse_moved,
    refresh_metric_plot,
    render_metric_series,
    request_metric_autoscale,
    update_metric_stats,
)

autoscale_trace_plot = autoscale_metric_plot
handle_trace_mouse_moved = handle_metric_mouse_moved
refresh_trace_plot = refresh_metric_plot
render_trace_series = render_metric_series
request_trace_autoscale = request_metric_autoscale
update_trace_stats = update_metric_stats
