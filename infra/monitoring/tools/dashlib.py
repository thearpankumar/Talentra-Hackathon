"""Tiny Grafana dashboard DSL used by gen_dashboards.py (Grafana 11 JSON, provisioned as code)."""
import json
import os

PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
AM = {"type": "alertmanager", "uid": "alertmanager"}

GREEN, YELLOW, ORANGE, RED, BLUE = "green", "#EAB839", "orange", "red", "blue"


def steps(*pairs):
    """steps((None,'green'),(70,'yellow'),(90,'red'))"""
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in pairs]}


OKBAD = steps((None, RED), (1, GREEN))      # 0 = bad, >=1 = good
BADOK = steps((None, GREEN), (1, RED))      # 0 = good, >=1 = bad
PCT_USED = steps((None, GREEN), (0.7, YELLOW), (0.85, RED))
PCT_USED_100 = steps((None, GREEN), (70, YELLOW), (85, RED))


class Dash:
    def __init__(self, uid, title, tags, desc="", refresh="30s", time_from="now-6h"):
        self.d = {
            "uid": uid, "title": title, "tags": ["talentra"] + tags, "description": desc,
            "editable": False, "graphTooltip": 1, "schemaVersion": 39, "version": 1, "timezone": "browser",
            "refresh": refresh, "time": {"from": time_from, "to": "now"},
            "templating": {"list": []}, "panels": [], "annotations": {"list": []}, "links": [],
            "fiscalYearStartMonth": 0, "liveNow": False,
        }
        self._id = 0
        self._x = 0
        self._y = 0
        self._rowh = 0
        self.add_link_bar()

    # ---- chrome ----
    def add_link_bar(self):
        self.d["links"] = [
            {"type": "dashboards", "title": "Talentra dashboards", "tags": ["talentra"], "asDropdown": True,
             "includeVars": False, "keepTime": True, "icon": "external link", "targetBlank": False},
            {"type": "link", "title": "Prometheus alerts", "url": "/alerting/list", "icon": "bolt", "targetBlank": False},
        ]

    def annotate(self, name, expr, color, ds=PROM, title="{{alertname}}", text="{{summary}}", tags=None, step="60s"):
        self.d["annotations"]["list"].append({
            "name": name, "datasource": ds, "enable": True, "iconColor": color, "expr": expr,
            "step": step, "titleFormat": title, "textFormat": text, "tagKeys": ",".join(tags or ["severity"]),
            "useValueForTime": False,
        })

    def std_annotations(self, restarts=True, backups=False):
        self.annotate("Alerts firing", 'ALERTS{alertstate="firing",severity=~"warning|critical",alertname!="Watchdog"}', "red")
        if restarts:
            self.annotate("Container restarts", 'changes(container_start_time_seconds{name!=""}[1m]) > 0', "orange",
                          title="restart: {{name}}", text="container restarted", tags=[])
            self.annotate("CockroachDB node restarts", 'resets(sys_uptime{job="cockroachdb"}[1m]) > 0', "purple",
                          title="crdb restart: {{instance}}", text="node restarted", tags=[])
        if backups:
            self.annotate("Backup succeeded", 'changes(talentra_backup_last_success_timestamp_seconds{op="backup"}[1m]) > 0', "green",
                          title="backup ok: {{type}} {{dest}}", text="", tags=[])

    # ---- variables ----
    def var_query(self, name, query, label=None, multi=True, include_all=True, ds=PROM, regex="", refresh=2, default_all=True, hide=0):
        v = {
            "name": name, "label": label or name, "type": "query", "datasource": ds,
            "query": {"query": query, "refId": f"var-{name}", "qryType": 1} if ds == PROM else {"query": query, "refId": f"var-{name}"},
            "refresh": refresh, "sort": 1, "multi": multi, "includeAll": include_all, "regex": regex, "hide": hide,
            "current": {"selected": True, "text": ["All"], "value": ["$__all"]} if (include_all and default_all) else {},
            "options": [],
        }
        if include_all and ds == PROM:     # Loki: let Grafana expand "All" to an explicit alternation (an empty-compatible matcher is rejected by Loki)
            v["allValue"] = ".*"
        self.d["templating"]["list"].append(v)

    def var_custom(self, name, values, label=None, default=None, multi=False):
        vals = values if isinstance(values, list) else values.split(",")
        default = default or vals[0]
        self.d["templating"]["list"].append({
            "name": name, "label": label or name, "type": "custom", "query": ",".join(vals), "multi": multi,
            "includeAll": False, "current": {"selected": True, "text": default, "value": default},
            "options": [{"selected": v == default, "text": v, "value": v} for v in vals],
        })

    def var_textbox(self, name, default="", label=None):
        self.d["templating"]["list"].append({
            "name": name, "label": label or name, "type": "textbox", "query": default,
            "current": {"selected": False, "text": default, "value": default}, "options": [{"selected": True, "text": default, "value": default}],
        })

    def var_interval(self, name="rate", values="1m,5m,15m,1h", default="5m"):
        self.d["templating"]["list"].append({
            "name": name, "label": "Rate window", "type": "interval", "query": values, "auto": False,
            "current": {"selected": True, "text": default, "value": default},
            "options": [{"selected": v == default, "text": v, "value": v} for v in values.split(",")],
        })

    # ---- layout ----
    def _place(self, w, h):
        if self._x + w > 24:
            self._x = 0
            self._y += self._rowh
            self._rowh = 0
        pos = {"x": self._x, "y": self._y, "w": w, "h": h}
        self._x += w
        self._rowh = max(self._rowh, h)
        return pos

    def row(self, title, collapsed=False):
        self._x = 0
        self._y += self._rowh
        self._rowh = 0
        self._id += 1
        self.d["panels"].append({"type": "row", "id": self._id, "title": title, "collapsed": False,
                                 "gridPos": {"x": 0, "y": self._y, "w": 24, "h": 1}, "panels": []})
        self._y += 1

    def _panel(self, kind, title, targets, w, h, desc="", ds=PROM, unit=None, extra=None, options=None, defaults=None, overrides=None, links=None, transformations=None):
        self._id += 1
        tg = []
        for i, t in enumerate(targets):
            if isinstance(t, str):
                t = (t, "")
            expr, legend = t[0], t[1]
            o = t[2] if len(t) > 2 else {}
            e = {"refId": chr(65 + i), "datasource": ds, "expr": expr, "legendFormat": legend}
            if ds == PROM:
                e.update({"range": True, "instant": False})
            e.update(o)
            tg.append(e)
        dflt = {"color": {"mode": "palette-classic"}}
        if unit:
            dflt["unit"] = unit
        dflt.update(defaults or {})
        p = {
            "id": self._id, "type": kind, "title": title, "description": desc, "datasource": ds,
            "gridPos": self._place(w, h), "targets": tg,
            "fieldConfig": {"defaults": dflt, "overrides": overrides or []},
            "options": options or {},
        }
        if links:
            p["links"] = links
        if transformations:
            p["transformations"] = transformations
        if extra:
            p.update(extra)
        self.d["panels"].append(p)
        return p

    def ts(self, title, targets, unit=None, w=12, h=8, desc="", stack=False, minv=None, maxv=None, thresholds=None,
           fill=10, legend="list", legend_calcs=None, ds=PROM, overrides=None, tooltip="multi", decimals=None, draw="line", links=None, axis_label=None):
        custom = {"drawStyle": draw, "lineWidth": 1, "fillOpacity": fill, "showPoints": "never", "spanNulls": False,
                  "stacking": {"mode": "normal" if stack else "none", "group": "A"}, "lineInterpolation": "linear",
                  "thresholdsStyle": {"mode": "line" if thresholds else "off"}, "axisPlacement": "auto"}
        if axis_label:
            custom["axisLabel"] = axis_label
        d = {"custom": custom}
        if minv is not None:
            d["min"] = minv
        if maxv is not None:
            d["max"] = maxv
        if decimals is not None:
            d["decimals"] = decimals
        if thresholds:
            d["thresholds"] = thresholds
        opts = {"legend": {"displayMode": "table" if legend == "table" else "list", "placement": "bottom" if legend != "right" else "right",
                           "showLegend": legend != "none", "calcs": legend_calcs or (["lastNotNull", "max"] if legend == "table" else [])},
                "tooltip": {"mode": tooltip, "sort": "desc"}}
        return self._panel("timeseries", title, targets, w, h, desc, ds, unit, options=opts, defaults=d, overrides=overrides, links=links)

    def stat(self, title, expr, unit=None, w=4, h=4, thresholds=None, desc="", mappings=None, graph=True, color_mode="background",
             decimals=None, calc="lastNotNull", ds=PROM, minv=None, maxv=None, text_mode="auto", links=None, legend=""):
        d = {"thresholds": thresholds or steps((None, GREEN)), "color": {"mode": "thresholds"}}
        if mappings:
            d["mappings"] = mappings
        if decimals is not None:
            d["decimals"] = decimals
        if minv is not None:
            d["min"] = minv
        if maxv is not None:
            d["max"] = maxv
        opts = {"reduceOptions": {"calcs": [calc], "fields": "", "values": False}, "colorMode": color_mode,
                "graphMode": "area" if graph else "none", "textMode": text_mode, "justifyMode": "auto", "orientation": "auto"}
        exprs = expr if isinstance(expr, list) else [(expr, legend)]
        return self._panel("stat", title, exprs, w, h, desc, ds, unit, options=opts, defaults=d, links=links)

    def gauge(self, title, expr, unit=None, w=4, h=5, thresholds=None, minv=0, maxv=None, desc="", ds=PROM, decimals=None, legend=""):
        d = {"thresholds": thresholds or steps((None, GREEN)), "color": {"mode": "thresholds"}, "min": minv}
        if maxv is not None:
            d["max"] = maxv
        if decimals is not None:
            d["decimals"] = decimals
        opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "showThresholdLabels": False, "showThresholdMarkers": True}
        exprs = expr if isinstance(expr, list) else [(expr, legend)]
        return self._panel("gauge", title, exprs, w, h, desc, ds, unit, options=opts, defaults=d)

    def bargauge(self, title, targets, unit=None, w=8, h=8, thresholds=None, minv=0, maxv=None, desc="", ds=PROM, mode="gradient", decimals=None):
        d = {"thresholds": thresholds or steps((None, GREEN)), "color": {"mode": "thresholds"}, "min": minv}
        if maxv is not None:
            d["max"] = maxv
        if decimals is not None:
            d["decimals"] = decimals
        opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "displayMode": mode, "orientation": "horizontal",
                "showUnfilled": True, "valueMode": "color"}
        tg = [(t[0], t[1], {"instant": True, "range": False}) if len(t) == 2 else t for t in targets]
        return self._panel("bargauge", title, tg, w, h, desc, ds, unit, options=opts, defaults=d)

    def table(self, title, targets, w=12, h=8, desc="", unit=None, overrides=None, rename=None, hide=None, sort_by=None, ds=PROM, defaults=None, merge=True):
        tg = []
        for t in targets:
            tg.append((t[0], t[1] if len(t) > 1 else "", {"format": "table", "instant": True, "range": False}))
        tr = []
        if merge and len(targets) > 1:
            tr.append({"id": "merge", "options": {}})
        ex = {"excludeByName": {k: True for k in (hide or ["Time", "__name__"])}, "renameByName": rename or {}}
        tr.append({"id": "organize", "options": ex})
        opts = {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}
        if sort_by:
            opts["sortBy"] = [{"displayName": sort_by, "desc": True}]
        d = {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "filterable": True}}
        d.update(defaults or {})
        return self._panel("table", title, tg, w, h, desc, ds, unit, options=opts, defaults=d, overrides=overrides, transformations=tr)

    def logs(self, title, expr, w=24, h=12, desc="", wrap=True):
        opts = {"showTime": True, "wrapLogMessage": wrap, "prettifyLogMessage": False, "enableLogDetails": True, "dedupStrategy": "none",
                "sortOrder": "Descending", "showLabels": False, "showCommonLabels": False}
        return self._panel("logs", title, [(expr, "", {"queryType": "range", "maxLines": 500})], w, h, desc, LOKI, None, options=opts, defaults={})

    def text(self, md, w=24, h=4, title=""):
        self._id += 1
        self.d["panels"].append({"id": self._id, "type": "text", "title": title, "gridPos": self._place(w, h),
                                 "options": {"mode": "markdown", "content": md}})

    def pie(self, title, targets, unit=None, w=8, h=8, desc=""):
        tg = [(t[0], t[1], {"instant": True, "range": False}) for t in targets]
        opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "pieType": "donut", "legend": {"displayMode": "table", "placement": "right", "values": ["value"]},
                "tooltip": {"mode": "single"}}
        return self._panel("piechart", title, tg, w, h, desc, PROM, unit, options=opts, defaults={})

    def statetimeline(self, title, targets, w=12, h=6, desc="", mappings=None, thresholds=None):
        d = {"custom": {"fillOpacity": 70, "lineWidth": 0}, "thresholds": thresholds or OKBAD, "color": {"mode": "thresholds"}}
        d["mappings"] = mappings or [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}]
        opts = {"showValue": "never", "mergeValues": True, "legend": {"showLegend": False}, "tooltip": {"mode": "single"}, "rowHeight": 0.8}
        return self._panel("state-timeline", title, targets, w, h, desc, PROM, None, options=opts, defaults=d)

    # ---- output ----
    def save(self, folder_dir):
        os.makedirs(folder_dir, exist_ok=True)
        path = os.path.join(folder_dir, self.d["uid"] + ".json")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.d, fh, indent=1, sort_keys=False)
            fh.write("\n")
        return path
