"""
report_generator.py - Generate PDF sales reports for manager WhatsApp reporting.
Uses fpdf2. Falls back to plain text if fpdf2 not installed.
"""
import os
import json
import tempfile
from datetime import datetime, timedelta, date
from collections import defaultdict

try:
    from fpdf import FPDF
    HAS_FPDF = True
except ImportError:
    HAS_FPDF = False


def _get_date_range(period: str, period_value: str = ""):
    """Return (start_dt, end_dt, label) for the given period."""
    now = datetime.utcnow()
    today = now.date()

    if period == "day":
        try:
            d = datetime.strptime(period_value.strip(), "%d/%m/%Y").date()
        except Exception:
            try:
                d = datetime.strptime(period_value.strip(), "%Y-%m-%d").date()
            except Exception:
                d = today
        start = datetime(d.year, d.month, d.day, 0, 0, 0)
        end = datetime(d.year, d.month, d.day, 23, 59, 59)
        label = d.strftime("%d %b %Y")

    elif period == "week_current":
        # Monday of current week → now
        monday = today - timedelta(days=today.weekday())
        start = datetime(monday.year, monday.month, monday.day, 0, 0, 0)
        end = now
        label = f"Current Week ({monday.strftime('%d %b')} – {today.strftime('%d %b %Y')})"

    elif period == "week_last7":
        seven_ago = today - timedelta(days=6)
        start = datetime(seven_ago.year, seven_ago.month, seven_ago.day, 0, 0, 0)
        end = now
        label = f"Last 7 Days ({seven_ago.strftime('%d %b')} – {today.strftime('%d %b %Y')})"

    elif period == "month":
        start = datetime(today.year, today.month, 1, 0, 0, 0)
        end = now
        label = today.strftime("%B %Y")

    else:  # all
        start = datetime(2000, 1, 1)
        end = now
        label = "All Time"

    return start, end, label


def _filter_orders(orders, start_dt, end_dt, feature):
    """Filter Order ORM objects by date range and delivery_type feature."""
    result = []
    delivery_map = {
        "delivery": "delivery",
        "car": "car_delivery",
        "qr": "dine_in",
        "reservation": None,  # handled separately
    }
    for o in orders:
        created = o.created_at
        if not (start_dt <= created <= end_dt):
            continue
        if feature in ("all", "ALL"):
            result.append(o)
        elif feature == "delivery" and getattr(o, "delivery_type", "") == "delivery":
            result.append(o)
        elif feature == "car" and getattr(o, "delivery_type", "") in ("car_delivery", "car"):
            result.append(o)
        elif feature == "qr" and getattr(o, "delivery_type", "") in ("dine_in", "qr"):
            result.append(o)
        elif feature == "reservation":
            pass  # reservations handled separately below
    return result


def _tally_items(orders):
    """Return {item_name: {qty, revenue}} sorted by revenue."""
    tally = defaultdict(lambda: {"qty": 0, "revenue": 0.0})
    for order in orders:
        try:
            items = json.loads(order.items_json or "[]")
            if isinstance(items, dict):
                items = list(items.values())
            for entry in items:
                if isinstance(entry, dict):
                    item = entry.get("item", {})
                    qty = entry.get("qty", 1)
                    name = item.get("name", "Unknown") if isinstance(item, dict) else str(item)
                    price = item.get("price", 0) if isinstance(item, dict) else 0
                    tally[name]["qty"] += qty
                    tally[name]["revenue"] += price * qty
        except Exception:
            pass
    return dict(sorted(tally.items(), key=lambda x: x[1]["revenue"], reverse=True))


def generate_report_pdf(orders, reservations, period_label, feature_label, owner_name="") -> str:
    """
    Generate a PDF report. Returns the file path of the saved PDF.
    orders: list of Order ORM objects (already filtered)
    reservations: list of Reservation ORM objects (already filtered, or empty)
    """
    total_orders = len(orders)
    total_revenue = sum(o.grand_total or 0 for o in orders)
    total_reservations = len(reservations)
    item_tally = _tally_items(orders)

    if HAS_FPDF:
        return _generate_fpdf(
            orders, reservations, period_label, feature_label,
            owner_name, total_orders, total_revenue, total_reservations, item_tally
        )
    else:
        return _generate_text_report(
            orders, reservations, period_label, feature_label,
            owner_name, total_orders, total_revenue, total_reservations, item_tally
        )


def _generate_fpdf(orders, reservations, period_label, feature_label,
                   owner_name, total_orders, total_revenue, total_reservations, item_tally):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Header
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_fill_color(34, 139, 34)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 14, "Wild Automation - Sales Report", new_x="LMARGIN", new_y="NEXT", align="C", fill=True)

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 7, f"Period: {period_label}   |   Filter: {feature_label}   |   Generated: {datetime.utcnow().strftime('%d %b %Y %H:%M')} UTC",
             new_x="LMARGIN", new_y="NEXT", align="C")
    if owner_name:
        pdf.cell(0, 6, f"Account: {owner_name}", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)

    # Summary row
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(220, 220, 220)
    col_w = pdf.epw / 3
    pdf.cell(col_w, 10, f"Total Orders: {total_orders}", border=1, align="C", fill=True)
    pdf.cell(col_w, 10, f"Revenue: ${total_revenue:.2f}", border=1, align="C", fill=True)
    pdf.cell(col_w, 10, f"Reservations: {total_reservations}", border=1, align="C", fill=True)
    pdf.ln(14)

    # Best-selling items table
    if item_tally:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Item Performance (sorted by revenue)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_fill_color(50, 100, 200)
        pdf.set_text_color(255, 255, 255)
        col_widths = [pdf.epw * 0.5, pdf.epw * 0.25, pdf.epw * 0.25]
        pdf.cell(col_widths[0], 8, "Item", border=1, fill=True)
        pdf.cell(col_widths[1], 8, "Qty Sold", border=1, align="C", fill=True)
        pdf.cell(col_widths[2], 8, "Revenue", border=1, align="C", fill=True)
        pdf.ln()

        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 9)
        items_list = list(item_tally.items())
        top_item = items_list[0][0] if items_list else ""
        for idx, (name, data) in enumerate(items_list):
            is_top = (name == top_item)
            if is_top:
                pdf.set_fill_color(255, 223, 0)
                fill = True
                pdf.set_font("Helvetica", "B", 9)
            else:
                pdf.set_fill_color(245, 245, 245) if idx % 2 == 0 else pdf.set_fill_color(255, 255, 255)
                fill = True
                pdf.set_font("Helvetica", "", 9)
            label = name[:50] + ("  ⭐ BEST SELLER" if is_top else "")
            pdf.cell(col_widths[0], 7, label, border=1, fill=fill)
            pdf.cell(col_widths[1], 7, str(data["qty"]), border=1, align="C", fill=fill)
            pdf.cell(col_widths[2], 7, f"${data['revenue']:.2f}", border=1, align="C", fill=fill)
            pdf.ln()
        pdf.ln(5)

    # Reservations table
    if reservations:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Reservations", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(100, 50, 200)
        pdf.set_text_color(255, 255, 255)
        rw = [pdf.epw * 0.25, pdf.epw * 0.25, pdf.epw * 0.15, pdf.epw * 0.15, pdf.epw * 0.2]
        for h, w in zip(["Name", "Phone", "Date", "Time", "Party"], rw):
            pdf.cell(w, 8, h, border=1, fill=True)
        pdf.ln()
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 9)
        for idx, r in enumerate(reservations):
            fill_color = (245, 245, 245) if idx % 2 == 0 else (255, 255, 255)
            pdf.set_fill_color(*fill_color)
            pdf.cell(rw[0], 7, str(r.customer_name or "")[:30], border=1, fill=True)
            pdf.cell(rw[1], 7, str(r.customer_phone or "")[:20], border=1, fill=True)
            pdf.cell(rw[2], 7, str(r.reservation_date or "")[:12], border=1, align="C", fill=True)
            pdf.cell(rw[3], 7, str(r.reservation_time or "")[:8], border=1, align="C", fill=True)
            pdf.cell(rw[4], 7, str(r.party_size or "")[:10], border=1, align="C", fill=True)
            pdf.ln()

    # Footer
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, "Generated by Wild Automation CRM", new_x="LMARGIN", new_y="NEXT", align="C")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", prefix="report_")
    pdf.output(tmp.name)
    tmp.close()
    return tmp.name


def _generate_text_report(orders, reservations, period_label, feature_label,
                           owner_name, total_orders, total_revenue, total_reservations, item_tally):
    """Fallback: plain text summary when fpdf2 is not installed."""
    lines = [
        "WILD AUTOMATION - SALES REPORT",
        f"Period: {period_label}",
        f"Filter: {feature_label}",
        f"Generated: {datetime.utcnow().strftime('%d %b %Y %H:%M')} UTC",
        "",
        f"Total Orders: {total_orders}",
        f"Total Revenue: ${total_revenue:.2f}",
        f"Reservations: {total_reservations}",
        "",
        "=== TOP ITEMS ===",
    ]
    for name, data in list(item_tally.items())[:20]:
        lines.append(f"  {name}: {data['qty']} sold, ${data['revenue']:.2f}")
    content = "\n".join(lines)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", prefix="report_", mode="w")
    tmp.write(content)
    tmp.close()
    return tmp.name


def build_text_summary(orders, reservations, period_label, feature_label):
    """Short WhatsApp text summary (no PDF)."""
    total_orders = len(orders)
    total_revenue = sum(o.grand_total or 0 for o in orders)
    item_tally = _tally_items(orders)
    top3 = list(item_tally.items())[:3]

    lines = [
        f"📊 *Sales Report*",
        f"📅 Period: {period_label}",
        f"🔖 Filter: {feature_label}",
        f"",
        f"📦 Orders: *{total_orders}*",
        f"💰 Revenue: *${total_revenue:.2f}*",
        f"🍽️ Reservations: *{len(reservations)}*",
    ]
    if top3:
        lines += ["", "⭐ *Top Items:*"]
        for i, (name, data) in enumerate(top3):
            lines.append(f"  {i+1}. {name} — {data['qty']} sold (${data['revenue']:.2f})")
    return "\n".join(lines)


def create_dummy_demo_pdf() -> str:
    """Create a demo PDF with sample data for presentation."""
    if not HAS_FPDF:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", prefix="demo_report_", mode="w")
        tmp.write("DEMO REPORT - fpdf2 not installed")
        tmp.close()
        return tmp.name

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 22)
    pdf.set_fill_color(34, 139, 34)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 16, "Wild Automation - DEMO Report", new_x="LMARGIN", new_y="NEXT", align="C", fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 7, "This is a sample report for demonstration purposes", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(6)

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(220, 220, 220)
    col = pdf.epw / 3
    pdf.cell(col, 10, "Total Orders: 142", border=1, align="C", fill=True)
    pdf.cell(col, 10, "Revenue: $3,891.50", border=1, align="C", fill=True)
    pdf.cell(col, 10, "Reservations: 18", border=1, align="C", fill=True)
    pdf.ln(14)

    demo_items = [
        ("Classic Cheeseburger", 58, 290.00),
        ("Pepperoni Pizza", 42, 420.00),
        ("Chicken Wings 6pc", 37, 259.00),
        ("BBQ Ribs Half Rack", 31, 465.00),
        ("Caesar Salad", 28, 168.00),
        ("Milkshake", 25, 100.00),
        ("Garlic Bread", 22, 88.00),
    ]

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Item Performance", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(50, 100, 200)
    pdf.set_text_color(255, 255, 255)
    cw = [pdf.epw * 0.5, pdf.epw * 0.25, pdf.epw * 0.25]
    pdf.cell(cw[0], 8, "Item", border=1, fill=True)
    pdf.cell(cw[1], 8, "Qty Sold", border=1, align="C", fill=True)
    pdf.cell(cw[2], 8, "Revenue", border=1, align="C", fill=True)
    pdf.ln()
    pdf.set_text_color(0, 0, 0)
    for idx, (name, qty, rev) in enumerate(demo_items):
        is_top = idx == 0
        if is_top:
            pdf.set_fill_color(255, 223, 0)
            pdf.set_font("Helvetica", "B", 9)
            label = f"{name}  ⭐ BEST SELLER"
        else:
            pdf.set_fill_color(245, 245, 245) if idx % 2 == 0 else pdf.set_fill_color(255, 255, 255)
            pdf.set_font("Helvetica", "", 9)
            label = name
        pdf.cell(cw[0], 7, label, border=1, fill=True)
        pdf.cell(cw[1], 7, str(qty), border=1, align="C", fill=True)
        pdf.cell(cw[2], 7, f"${rev:.2f}", border=1, align="C", fill=True)
        pdf.ln()

    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, "Generated by Wild Automation CRM  |  Demo Data Only", new_x="LMARGIN", new_y="NEXT", align="C")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", prefix="demo_report_")
    pdf.output(tmp.name)
    tmp.close()
    return tmp.name
