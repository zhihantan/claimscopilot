#!/usr/bin/env python3
"""
Lakeview Dashboard Builder

A Python helper class for programmatically creating Databricks Lakeview dashboards.

Usage:
    from lakeview_builder import LakeviewDashboard

    dashboard = LakeviewDashboard("My Dashboard")
    dashboard.add_dataset("sales", "Sales Data", "SELECT * FROM catalog.schema.sales")
    dashboard.add_bar_chart(...)
    json_payload = dashboard.to_json()
"""

import json
import uuid
from typing import Optional, List, Dict, Any


class LakeviewDashboard:
    """Builder class for creating Lakeview dashboard JSON payloads."""

    # Default color palette
    DEFAULT_COLORS = [
        "#FFAB00", "#00A972", "#FF3621", "#8BCAE7",
        "#AB4057", "#99DDB4", "#FCA4A1", "#919191", "#BF7080"
    ]

    def __init__(self, name: str = "New Dashboard"):
        """Initialize a new dashboard.

        Args:
            name: Display name for the dashboard
        """
        self.name = name
        self.datasets: List[Dict] = []
        self.pages: List[Dict] = []
        self._current_page: Optional[Dict] = None

        # Create default page
        self.add_page("Overview")

    @staticmethod
    def _generate_id() -> str:
        """Generate a unique 8-character hex ID."""
        return uuid.uuid4().hex[:8]

    def add_dataset(self, name: str, display_name: str, query: str) -> str:
        """Add a dataset (SQL query) to the dashboard.

        Args:
            name: Unique identifier for the dataset
            display_name: Human-readable name
            query: SQL query string

        Returns:
            The dataset name for reference in widgets
        """
        dataset = {
            "name": name,
            "displayName": display_name,
            "queryLines": [query]
        }
        self.datasets.append(dataset)
        return name

    def add_page(self, display_name: str) -> str:
        """Add a new page to the dashboard.

        Args:
            display_name: Name shown in the tab

        Returns:
            The page ID
        """
        page_id = self._generate_id()
        page = {
            "name": page_id,
            "displayName": display_name,
            "pageType": "PAGE_TYPE_CANVAS",
            "layout": []
        }
        self.pages.append(page)
        self._current_page = page
        return page_id

    def _add_widget(self, widget: Dict, position: Dict[str, int]) -> None:
        """Add a widget to the current page.

        Args:
            widget: Widget configuration
            position: Position dict with x, y, width, height
        """
        if self._current_page is None:
            raise ValueError("No page exists. Call add_page() first.")

        layout_item = {
            "widget": widget,
            "position": {
                "x": position.get("x", 0),
                "y": position.get("y", 0),
                "width": position.get("width", 2),
                "height": position.get("height", 3)
            }
        }
        self._current_page["layout"].append(layout_item)

    def _create_field(self, name: str, expression: str) -> Dict:
        """Create a field definition."""
        return {"name": name, "expression": expression}

    def add_bar_chart(
        self,
        dataset_name: str,
        x_field: str,
        y_field: str,
        y_agg: str = "SUM",
        title: Optional[str] = None,
        position: Optional[Dict[str, int]] = None,
        colors: Optional[List[str]] = None,
        show_labels: bool = True,
        color_field: Optional[str] = None,
        sort_descending: bool = False
    ) -> str:
        """Add a bar chart widget.

        Args:
            dataset_name: Name of the dataset to use
            x_field: Field for X axis (categorical)
            y_field: Field for Y axis (will be aggregated)
            y_agg: Aggregation function (SUM, COUNT, AVG, MIN, MAX)
            title: Chart title
            position: Position dict with x, y, width, height
            colors: List of hex color codes
            show_labels: Whether to show value labels
            color_field: Optional field for color grouping
            sort_descending: Sort bars by y-axis value in descending order.
                Use this for "Top K" or "Most X" visualizations.

        Returns:
            The widget ID
        """
        widget_id = self._generate_id()

        # Build fields
        y_name = f"{y_agg.lower()}({y_field})"
        fields = [
            self._create_field(x_field, f"`{x_field}`"),
            self._create_field(y_name, f"{y_agg}(`{y_field}`)")
        ]

        if color_field:
            fields.append(self._create_field(color_field, f"`{color_field}`"))

        # Build x-axis scale with optional sorting
        # Note: Lakeview uses "y-reversed" to show largest values first (left-to-right)
        x_scale = {"type": "categorical"}
        if sort_descending:
            x_scale["sort"] = {"by": "y-reversed"}

        # Build encodings
        encodings = {
            "x": {
                "fieldName": x_field,
                "scale": x_scale,
                "displayName": x_field
            },
            "y": {
                "fieldName": y_name,
                "scale": {"type": "quantitative"},
                "displayName": f"{y_agg} of {y_field}"
            },
            "label": {"show": show_labels}
        }

        if color_field:
            encodings["color"] = {
                "fieldName": color_field,
                "scale": {"type": "categorical"},
                "displayName": color_field
            }

        widget = {
            "name": widget_id,
            "queries": [{
                "name": "main_query",
                "query": {
                    "datasetName": dataset_name,
                    "fields": fields,
                    "disaggregated": False
                }
            }],
            "spec": {
                "version": 3,
                "widgetType": "bar",
                "encodings": encodings,
                "frame": {
                    "showTitle": title is not None,
                    "title": title or ""
                },
                "mark": {
                    "colors": colors or self.DEFAULT_COLORS
                }
            }
        }

        self._add_widget(widget, position or {"x": 0, "y": 0, "width": 3, "height": 4})
        return widget_id

    def add_line_chart(
        self,
        dataset_name: str,
        x_field: str,
        y_field: str,
        y_agg: str = "SUM",
        time_grain: Optional[str] = None,
        title: Optional[str] = None,
        position: Optional[Dict[str, int]] = None,
        color_field: Optional[str] = None
    ) -> str:
        """Add a line chart widget.

        Args:
            dataset_name: Name of the dataset to use
            x_field: Field for X axis (temporal or categorical)
            y_field: Field for Y axis (will be aggregated)
            y_agg: Aggregation function
            time_grain: Time granularity (MONTH, WEEK, DAY, HOUR)
            title: Chart title
            position: Position dict
            color_field: Optional field for multiple series

        Returns:
            The widget ID
        """
        widget_id = self._generate_id()

        # Build X field expression
        if time_grain:
            x_name = f"{time_grain.lower()}({x_field})"
            x_expr = f'DATE_TRUNC("{time_grain}", `{x_field}`)'
            x_scale_type = "temporal"
        else:
            x_name = x_field
            x_expr = f"`{x_field}`"
            x_scale_type = "categorical"

        y_name = f"{y_agg.lower()}({y_field})"

        fields = [
            self._create_field(x_name, x_expr),
            self._create_field(y_name, f"{y_agg}(`{y_field}`)")
        ]

        if color_field:
            fields.append(self._create_field(color_field, f"`{color_field}`"))

        encodings = {
            "x": {
                "fieldName": x_name,
                "scale": {"type": x_scale_type},
                "displayName": x_field
            },
            "y": {
                "fieldName": y_name,
                "scale": {"type": "quantitative"},
                "displayName": f"{y_agg} of {y_field}"
            }
        }

        if color_field:
            encodings["color"] = {
                "fieldName": color_field,
                "scale": {"type": "categorical"},
                "displayName": color_field
            }

        widget = {
            "name": widget_id,
            "queries": [{
                "name": "main_query",
                "query": {
                    "datasetName": dataset_name,
                    "fields": fields,
                    "disaggregated": False
                }
            }],
            "spec": {
                "version": 3,
                "widgetType": "line",
                "encodings": encodings,
                "frame": {
                    "showTitle": title is not None,
                    "title": title or ""
                }
            }
        }

        self._add_widget(widget, position or {"x": 0, "y": 0, "width": 3, "height": 4})
        return widget_id

    def add_pie_chart(
        self,
        dataset_name: str,
        angle_field: str,
        color_field: str,
        angle_agg: str = "COUNT",
        title: Optional[str] = None,
        position: Optional[Dict[str, int]] = None
    ) -> str:
        """Add a pie chart widget.

        Args:
            dataset_name: Name of the dataset to use
            angle_field: Field for slice size (will be aggregated)
            color_field: Field for slice categories
            angle_agg: Aggregation function
            title: Chart title
            position: Position dict

        Returns:
            The widget ID
        """
        widget_id = self._generate_id()

        if angle_agg == "COUNT":
            angle_name = "count(*)"
            angle_expr = "COUNT(`*`)"
        else:
            angle_name = f"{angle_agg.lower()}({angle_field})"
            angle_expr = f"{angle_agg}(`{angle_field}`)"

        widget = {
            "name": widget_id,
            "queries": [{
                "name": "main_query",
                "query": {
                    "datasetName": dataset_name,
                    "fields": [
                        self._create_field(angle_name, angle_expr),
                        self._create_field(color_field, f"`{color_field}`")
                    ],
                    "disaggregated": False
                }
            }],
            "spec": {
                "version": 3,
                "widgetType": "pie",
                "encodings": {
                    "angle": {
                        "fieldName": angle_name,
                        "scale": {"type": "quantitative"},
                        "displayName": f"{angle_agg} of Records" if angle_agg == "COUNT" else f"{angle_agg} of {angle_field}"
                    },
                    "color": {
                        "fieldName": color_field,
                        "scale": {"type": "categorical"},
                        "displayName": color_field
                    }
                },
                "frame": {
                    "showTitle": title is not None,
                    "title": title or ""
                }
            }
        }

        self._add_widget(widget, position or {"x": 0, "y": 0, "width": 2, "height": 4})
        return widget_id

    def add_counter(
        self,
        dataset_name: str,
        value_field: str,
        value_agg: str = "SUM",
        title: Optional[str] = None,
        position: Optional[Dict[str, int]] = None
    ) -> str:
        """Add a counter/KPI widget.

        Args:
            dataset_name: Name of the dataset to use
            value_field: Field to display
            value_agg: Aggregation function (SUM, COUNT, AVG, etc.)
            title: Widget title
            position: Position dict

        Returns:
            The widget ID
        """
        widget_id = self._generate_id()

        if value_agg == "COUNT":
            value_name = "count(*)"
            value_expr = "COUNT(`*`)"
        else:
            value_name = f"{value_agg.lower()}({value_field})"
            value_expr = f"{value_agg}(`{value_field}`)"

        widget = {
            "name": widget_id,
            "queries": [{
                "name": "main_query",
                "query": {
                    "datasetName": dataset_name,
                    "fields": [
                        self._create_field(value_name, value_expr)
                    ],
                    "disaggregated": True
                }
            }],
            "spec": {
                "version": 2,
                "widgetType": "counter",
                "encodings": {
                    "value": {
                        "fieldName": value_name,
                        "displayName": title or value_name
                    }
                },
                "frame": {
                    "showTitle": title is not None,
                    "title": title or ""
                }
            }
        }

        self._add_widget(widget, position or {"x": 0, "y": 0, "width": 1, "height": 2})
        return widget_id

    def add_scatter_plot(
        self,
        dataset_name: str,
        x_field: str,
        y_field: str,
        title: Optional[str] = None,
        position: Optional[Dict[str, int]] = None,
        color_field: Optional[str] = None,
        colors: Optional[List[str]] = None
    ) -> str:
        """Add a scatter plot widget.

        Args:
            dataset_name: Name of the dataset to use
            x_field: Field for X axis
            y_field: Field for Y axis
            title: Chart title
            position: Position dict
            color_field: Optional field for color grouping
            colors: Custom color palette

        Returns:
            The widget ID
        """
        widget_id = self._generate_id()

        fields = [
            self._create_field(x_field, f"`{x_field}`"),
            self._create_field(y_field, f"`{y_field}`")
        ]

        if color_field:
            fields.append(self._create_field(color_field, f"`{color_field}`"))

        encodings = {
            "x": {
                "fieldName": x_field,
                "scale": {"type": "quantitative"},
                "displayName": x_field
            },
            "y": {
                "fieldName": y_field,
                "scale": {"type": "quantitative"},
                "displayName": y_field
            }
        }

        if color_field:
            encodings["color"] = {
                "fieldName": color_field,
                "scale": {"type": "categorical"},
                "displayName": color_field
            }

        spec = {
            "version": 3,
            "widgetType": "scatter",
            "encodings": encodings,
            "frame": {
                "showTitle": title is not None,
                "title": title or ""
            }
        }

        if colors:
            spec["mark"] = {"colors": colors}

        widget = {
            "name": widget_id,
            "queries": [{
                "name": "main_query",
                "query": {
                    "datasetName": dataset_name,
                    "fields": fields,
                    "disaggregated": True
                }
            }],
            "spec": spec
        }

        self._add_widget(widget, position or {"x": 0, "y": 0, "width": 3, "height": 4})
        return widget_id

    def add_table(
        self,
        dataset_name: str,
        columns: List[Dict[str, Any]],
        title: Optional[str] = None,
        position: Optional[Dict[str, int]] = None
    ) -> str:
        """Add a table widget.

        Args:
            dataset_name: Name of the dataset to use
            columns: List of column definitions, each with:
                - field: Field name
                - title: Display title (optional)
                - type: Data type (string, integer, float, datetime)
                - format: Number format for numeric types (optional)
            title: Widget title
            position: Position dict

        Returns:
            The widget ID
        """
        widget_id = self._generate_id()

        fields = []
        column_encodings = []

        for i, col in enumerate(columns):
            field_name = col["field"]
            fields.append(self._create_field(field_name, f"`{field_name}`"))

            col_type = col.get("type", "string")
            display_as = "string"
            align = "left"

            if col_type in ("integer", "float"):
                display_as = "number"
                align = "right"
            elif col_type == "datetime":
                display_as = "datetime"
                align = "right"

            encoding = {
                "fieldName": field_name,
                "type": col_type,
                "displayAs": display_as,
                "title": col.get("title", field_name),
                "displayName": col.get("title", field_name),
                "order": 100000 + i,
                "alignContent": align
            }

            if "format" in col and col_type in ("integer", "float"):
                encoding["numberFormat"] = col["format"]

            column_encodings.append(encoding)

        widget = {
            "name": widget_id,
            "queries": [{
                "name": "main_query",
                "query": {
                    "datasetName": dataset_name,
                    "fields": fields,
                    "disaggregated": True
                }
            }],
            "spec": {
                "version": 1,
                "widgetType": "table",
                "encodings": {
                    "columns": column_encodings
                },
                "frame": {
                    "showTitle": title is not None,
                    "title": title or ""
                }
            }
        }

        self._add_widget(widget, position or {"x": 0, "y": 0, "width": 6, "height": 5})
        return widget_id

    def add_filter_dropdown(
        self,
        dataset_name: str,
        field: str,
        title: Optional[str] = None,
        position: Optional[Dict[str, int]] = None,
        multi_select: bool = False
    ) -> str:
        """Add a dropdown filter widget.

        Args:
            dataset_name: Name of the dataset to use
            field: Field to filter on
            title: Filter title
            position: Position dict
            multi_select: Allow multiple selections

        Returns:
            The widget ID
        """
        widget_id = self._generate_id()
        query_name = f"filter_{widget_id}_{field}"

        widget_type = "filter-multi-select" if multi_select else "filter-single-select"

        widget = {
            "name": widget_id,
            "queries": [{
                "name": query_name,
                "query": {
                    "datasetName": dataset_name,
                    "fields": [
                        self._create_field(field, f"`{field}`"),
                        self._create_field(f"{field}_associativity", 'COUNT_IF(`associative_filter_predicate_group`)')
                    ],
                    "disaggregated": False
                }
            }],
            "spec": {
                "version": 2,
                "widgetType": widget_type,
                "encodings": {
                    "fields": [{
                        "fieldName": field,
                        "displayName": field,
                        "queryName": query_name
                    }]
                },
                "frame": {
                    "showTitle": title is not None,
                    "title": title or f"Filter by {field}"
                }
            }
        }

        self._add_widget(widget, position or {"x": 0, "y": 0, "width": 1, "height": 2})
        return widget_id

    def add_date_filter(
        self,
        dataset_name: str,
        field: str,
        title: Optional[str] = None,
        position: Optional[Dict[str, int]] = None
    ) -> str:
        """Add a date range picker filter widget.

        Args:
            dataset_name: Name of the dataset to use
            field: Date field to filter on
            title: Filter title
            position: Position dict

        Returns:
            The widget ID
        """
        widget_id = self._generate_id()
        query_name = f"filter_{widget_id}_{field}"

        widget = {
            "name": widget_id,
            "queries": [{
                "name": query_name,
                "query": {
                    "datasetName": dataset_name,
                    "fields": [
                        self._create_field(field, f"`{field}`"),
                        self._create_field(f"{field}_associativity", 'COUNT_IF(`associative_filter_predicate_group`)')
                    ],
                    "disaggregated": False
                }
            }],
            "spec": {
                "version": 2,
                "widgetType": "filter-date-range-picker",
                "encodings": {
                    "fields": [{
                        "fieldName": field,
                        "displayName": field,
                        "queryName": query_name
                    }]
                },
                "frame": {
                    "showTitle": title is not None,
                    "title": title or f"Select {field}"
                }
            }
        }

        self._add_widget(widget, position or {"x": 0, "y": 0, "width": 1, "height": 2})
        return widget_id

    def to_dict(self) -> Dict:
        """Convert dashboard to dictionary format.

        Returns:
            Dictionary representation of the dashboard
        """
        return {
            "datasets": self.datasets,
            "pages": self.pages,
            "uiSettings": {
                "theme": {
                    "widgetHeaderAlignment": "ALIGNMENT_UNSPECIFIED"
                },
                "applyModeEnabled": False
            }
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert dashboard to JSON string.

        Args:
            indent: JSON indentation level

        Returns:
            JSON string for the serialized_dashboard field
        """
        return json.dumps(self.to_dict(), indent=indent)

    def get_api_payload(self, warehouse_id: str, parent_path: str) -> Dict:
        """Get the full API payload for creating the dashboard.

        Args:
            warehouse_id: SQL warehouse ID
            parent_path: Path where dashboard will be created (e.g., /Users/user@email.com)

        Returns:
            Complete API payload dictionary
        """
        return {
            "display_name": self.name,
            "warehouse_id": warehouse_id,
            "parent_path": parent_path,
            "serialized_dashboard": self.to_json()
        }


if __name__ == "__main__":
    # Example usage
    dashboard = LakeviewDashboard("Sales Analytics Dashboard")

    # Add dataset
    dashboard.add_dataset(
        "sales",
        "Sales Data",
        "SELECT * FROM main.lakeview_dashboard_test.sales"
    )

    # Add filters row
    dashboard.add_filter_dropdown(
        "sales", "category", "Category",
        position={"x": 0, "y": 0, "width": 1, "height": 2}
    )
    dashboard.add_filter_dropdown(
        "sales", "region", "Region",
        position={"x": 1, "y": 0, "width": 1, "height": 2},
        multi_select=True
    )
    dashboard.add_date_filter(
        "sales", "sale_date", "Date Range",
        position={"x": 2, "y": 0, "width": 1, "height": 2}
    )

    # Add KPIs row
    dashboard.add_counter(
        "sales", "amount", "SUM", "Total Revenue",
        position={"x": 0, "y": 2, "width": 1, "height": 2}
    )
    dashboard.add_counter(
        "sales", "quantity", "SUM", "Total Quantity",
        position={"x": 1, "y": 2, "width": 1, "height": 2}
    )
    dashboard.add_counter(
        "sales", "id", "COUNT", "Total Transactions",
        position={"x": 2, "y": 2, "width": 1, "height": 2}
    )

    # Add charts row
    dashboard.add_bar_chart(
        "sales", "category", "amount", "SUM",
        "Revenue by Category",
        position={"x": 0, "y": 4, "width": 3, "height": 4}
    )
    dashboard.add_pie_chart(
        "sales", "id", "region", "COUNT",
        "Distribution by Region",
        position={"x": 3, "y": 4, "width": 3, "height": 4}
    )

    # Add line chart
    dashboard.add_line_chart(
        "sales", "sale_date", "amount", "SUM",
        time_grain="MONTH",
        title="Monthly Revenue Trend",
        position={"x": 0, "y": 8, "width": 6, "height": 4},
        color_field="category"
    )

    # Print JSON
    print(dashboard.to_json())
