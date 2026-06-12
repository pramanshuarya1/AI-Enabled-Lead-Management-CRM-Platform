"""
CSV/Excel parsers for each campaign type.
Each parser returns a list of dicts ready for Supabase insert.
"""
import csv
import io
import re
import unicodedata
from datetime import datetime


# ============================================================
# UTILITY
# ============================================================

def clean_phone(phone: str) -> str:
    """Normalize phone number to digits only."""
    if not phone:
        return ""
    return re.sub(r'\D', '', str(phone).strip())


def make_unique_key(phone: str, bootcamp_title: str) -> str:
    """Composite dedup key: phone + bootcamp_title (lowercased, stripped)."""
    p = clean_phone(phone)
    b = unicodedata.normalize("NFKD", str(bootcamp_title or "").strip().lower())
    b = re.sub(r'\s+', '_', b)
    return f"{p}_{b}"


def clean_cell_value(val) -> str:
    """Normalize cell value. Return empty string if value represents null/empty placeholder."""
    if val is None:
        return ""
    v = str(val).strip()
    if v.upper() in ["EMPTY", "NULL", "NONE", "N/A"]:
        return ""
    return v


def get_flexible(row: dict, *keys) -> str:
    """
    Case-insensitive, space-insensitive, and underscore-insensitive key getter.
    Prioritizes the keys in the order they are provided.
    If the value contains ' | ', it splits and returns the first part as the name.
    """
    for key in keys:
        for k, v in row.items():
            if not k:
                continue
            k_clean = k.strip().lower().replace(' ', '').replace('_', '')
            key_clean = key.lower().replace(' ', '').replace('_', '')
            val = ""
            if k_clean == key_clean:
                val = str(v or "").strip()
            elif key_clean == 'name' and (k_clean.startswith('name|') or k_clean == 'name'):
                val = str(v or "").strip()
            
            if val:
                if ' | ' in val and key_clean in ['name', 'customername', 'leadname']:
                    val = val.split(' | ', 1)[0].strip()
                return clean_cell_value(val)
    return ""


def detect_bootcamp_from_row(row: dict) -> str:
    """
    Scans row columns to find any ' | ' separator.
    Checks the key (header) first (e.g. 'name | Atpitch_STA_June8_9' -> 'Atpitch_STA_June8_9').
    Checks the cell value next (e.g. 'Satyajit | AtPitch_June10_Lal Kitaab_Up' -> 'AtPitch_June10_Lal Kitaab_Up').
    """
    placeholders = ['bootcamptitle', 'bootcamp', 'bootcamp title', 'none', 'null', 'empty', '']
    for k, v in row.items():
        if not k:
            continue
        if ' | ' in k:
            parts = k.split(' | ', 1)
            if len(parts) == 2 and parts[1].strip() and parts[0].strip().lower() == 'name':
                b_name = parts[1].strip()
                if b_name.lower() not in placeholders:
                    return clean_cell_value(b_name)
        val = str(v or "").strip()
        if ' | ' in val:
            parts = val.split(' | ', 1)
            if len(parts) == 2 and parts[1].strip():
                b_name = parts[1].strip()
                if b_name.lower() not in placeholders:
                    return clean_cell_value(b_name)
    return ""



def detect_lead_type_atpitch(lead_name: str) -> str:
    """
    Categorize Atpitch leads into SIA, STA, or Others.
    SIA = Super Investing Arena / Super Investing
    STA = Stock Trading Arena / Short-term / Options
    """
    if not lead_name:
        return "atpitch_others"
    ln = lead_name.lower()
    sia_keywords = [
        "super investing", "sia", "si subscription", "model portfolio",
        "akshay", "investing subscription"
    ]
    sta_keywords = [
        "sta", "stock trading", "short term", "options", "trading arena",
        "derivatives", "futures"
    ]
    for kw in sia_keywords:
        if kw in ln:
            return "atpitch_sia"
    for kw in sta_keywords:
        if kw in ln:
            return "atpitch_sta"
    return "atpitch_others"


def detect_fp_level(course_level: str, lead_name: str = "", default_level: str = "fp_l1") -> str:
    """
    FP levels:
    - FP_L1 / FP_L1_High  → fp_l1
    - FP_L2 / FP_L1_Low   → fp_l2
    """
    if not course_level:
        course_level = ""
    cl = course_level.strip().upper()
    ln = (lead_name or "").upper()

    if "L2" in cl or "L1_LOW" in cl or "L1 LOW" in cl:
        return "fp_l2"
    if "L1_HIGH" in cl or "L1 HIGH" in cl:
        return "fp_l1"
    if "L1" in cl:
        return "fp_l1"
    if "HIGH" in ln:
        return "fp_l1"
    if "LOW" in ln:
        return "fp_l2"
    # Default FP goes to selected level
    return default_level


def parse_date(val):
    """Try to parse a date string into ISO format."""
    if not val:
        return None
    val = str(val).strip()
    for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(val, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_time(val):
    """Try to parse a time string into HH:MM:SS format."""
    if not val:
        return None
    val = str(val).strip()
    for fmt in ["%I:%M %p", "%I:%M:%S %p", "%H:%M:%S", "%H:%M", "%I %p"]:
        try:
            return datetime.strptime(val, fmt).time().isoformat()
        except ValueError:
            continue
    m = re.match(r'^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?$', val, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        m_val = int(m.group(2))
        s_val = int(m.group(3)) if m.group(3) else 0
        ampm = m.group(4)
        if ampm:
            ampm = ampm.upper()
            if ampm == 'PM' and h < 12:
                h += 12
            elif ampm == 'AM' and h == 12:
                h = 0
        try:
            from datetime import time
            return time(h, m_val, s_val).isoformat()
        except ValueError:
            pass
    return None


def read_csv_text(file_content: bytes, delimiter=',') -> list[dict]:
    """Read CSV bytes into list of dicts."""
    text = file_content.decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [row for row in reader]


def detect_delimiter(file_content: bytes) -> str:
    """Auto-detect CSV delimiter (comma vs tab)."""
    text = file_content.decode('utf-8-sig', errors='replace')[:2000]
    tabs = text.count('\t')
    commas = text.count(',')
    return '\t' if tabs > commas else ','


# ============================================================
# PARSER: ATPITCH (SIA / STA / Others)
# ============================================================
# Expected columns: Name, Contact No., Priority, Lead Name, joining_duration,
# Calling For Upsell, Calling Date, Agent, Status, Disposition, Status, Comments,
# Attempt, Follow Up Status1, Follow Up Comments 1, ...

def parse_atpitch(
    file_content: bytes,
    uploaded_by: str = None,
    batch_id: str = None,
    extra: dict = None
) -> tuple[list, list]:
    """
    Parse Atpitch CSV/TSV.
    Returns (leads_list, errors_list)
    """
    extra = extra or {}
    admin_date     = clean_cell_value(extra.get('extra_date', ''))
    admin_priority = clean_cell_value(extra.get('extra_priority', ''))
    admin_bootcamp = clean_cell_value(extra.get('extra_bootcamp', ''))
    admin_time     = clean_cell_value(extra.get('extra_time', ''))

    delimiter = detect_delimiter(file_content)
    rows = read_csv_text(file_content, delimiter)
    leads = []
    errors = []

    for i, row in enumerate(rows, start=2):
        try:
            phone = clean_phone(get_flexible(row, 'contact_no', 'phone', 'mobile', 'contact'))
            name = get_flexible(row, 'name', 'customer_name', 'lead_name')
            
            detected_bootcamp = detect_bootcamp_from_row(row)
            bootcamp_title = (
                admin_bootcamp
                or detected_bootcamp
                or name
                or 'Unknown Bootcamp'
            )

            if not phone:
                errors.append({"row": i, "error": "Missing phone number", "data": dict(row)})
                continue

            unique_key = make_unique_key(phone, bootcamp_title)
            campaign_type = detect_lead_type_atpitch(bootcamp_title)

            bootcamp_date_str = get_flexible(row, 'calling_date', 'date', 'dummy_date_time', 'created_on')
            bootcamp_date = parse_date(bootcamp_date_str) or admin_date or bootcamp_date_str or None
            bootcamp_time_str = get_flexible(row, 'fptime', 'fp_time', 'start_time', 'time')
            bootcamp_time = parse_time(bootcamp_time_str) or parse_time(admin_time) or None

            lead = {
                "unique_key": unique_key,
                "campaign_type": campaign_type,
                "lead_type": campaign_type.upper().replace("ATPITCH_", ""),
                "lead_name": name,
                "contact_no": phone,
                "bootcamp_title": bootcamp_title,
                "bootcamp_date": bootcamp_date,
                "fp_time": bootcamp_time,
                "agent_name": get_flexible(row, 'agent', 'agent_name', 'owner_user_name', 'owner'),
                "priority": admin_priority or get_flexible(row, 'priority') or None,
                "calling_for_upsell": get_flexible(row, 'calling_for_upsell'),
                "joining_duration": get_flexible(row, 'joining_duration'),
                "final_status": get_flexible(row, 'status') or "Pending",
                "uploaded_by": uploaded_by,
                "upload_batch": batch_id,
                "raw_data": {k.strip(): v for k, v in row.items() if k},
            }
            leads.append(lead)
        except Exception as e:
            errors.append({"row": i, "error": str(e), "data": dict(row)})

    return leads, errors



# ============================================================
# PARSER: UPSELL
# ============================================================
# Expected columns: name, phone, Lead Name, Bx, Date, Agent, Priority,
# Final Status, 1st Call Date, 1st Call Status, ...

def parse_upsell(
    file_content: bytes,
    uploaded_by: str = None,
    batch_id: str = None,
    extra: dict = None
) -> tuple[list, list]:
    extra = extra or {}
    admin_date     = clean_cell_value(extra.get('extra_date', ''))
    admin_priority = clean_cell_value(extra.get('extra_priority', ''))
    admin_bootcamp = clean_cell_value(extra.get('extra_bootcamp', ''))
    admin_time     = clean_cell_value(extra.get('extra_time', ''))

    delimiter = detect_delimiter(file_content)
    rows = read_csv_text(file_content, delimiter)
    leads = []
    errors = []

    for i, row in enumerate(rows, start=2):
        try:
            phone = clean_phone(get_flexible(row, 'phone', 'contact_no', 'mobile'))
            name = get_flexible(row, 'name', 'customer_name', 'lead_name')
            
            detected_bootcamp = detect_bootcamp_from_row(row)
            bootcamp_title = (
                admin_bootcamp
                or get_flexible(row, 'bx')
                or detected_bootcamp
                or name
                or 'Unknown Bootcamp'
            )

            if not phone:
                errors.append({"row": i, "error": "Missing phone", "data": dict(row)})
                continue

            unique_key = make_unique_key(phone, bootcamp_title)

            bootcamp_date_str = get_flexible(row, 'date', 'dummy_date_time', 'created_on')
            bootcamp_date = parse_date(bootcamp_date_str) or admin_date or bootcamp_date_str or None
            bootcamp_time_str = get_flexible(row, 'fptime', 'fp_time', 'start_time', 'time')
            bootcamp_time = parse_time(bootcamp_time_str) or parse_time(admin_time) or None

            lead = {
                "unique_key": unique_key,
                "campaign_type": "upsell",
                "lead_type": "Upsell",
                "lead_name": name,
                "contact_no": phone,
                "bootcamp_title": bootcamp_title,
                "bootcamp_date": bootcamp_date,
                "fp_time": bootcamp_time,
                "agent_name": get_flexible(row, 'agent', 'agent_name', 'owner_user_name', 'owner'),
                "priority": admin_priority or get_flexible(row, 'priority') or None,
                "final_status": get_flexible(row, 'final_status', 'status') or "Pending",
                "uploaded_by": uploaded_by,
                "upload_batch": batch_id,
                "raw_data": {k.strip(): v for k, v in row.items() if k},
            }
            leads.append(lead)
        except Exception as e:
            errors.append({"row": i, "error": str(e), "data": dict(row)})

    return leads, errors


# ============================================================
# PARSER: FAILED PENDING (FP)
# ============================================================
# Expected columns: lead name, phone, name, email, amount, status,
# CourseLevel, comment, couponCode, bootcampTitle, paymentMethodType,
# fpDate, fpTime, Lead set, Agent, Lead type, ...

def parse_failed_pending(
    file_content: bytes,
    uploaded_by: str = None,
    batch_id: str = None,
    extra: dict = None,
) -> tuple[list, list]:
    extra = extra or {}
    admin_date     = clean_cell_value(extra.get('extra_date', ''))
    admin_priority = clean_cell_value(extra.get('extra_priority', ''))
    admin_bootcamp = clean_cell_value(extra.get('extra_bootcamp', ''))
    admin_time     = clean_cell_value(extra.get('extra_time', ''))

    delimiter = detect_delimiter(file_content)
    rows = read_csv_text(file_content, delimiter)
    leads = []
    errors = []

    for i, row in enumerate(rows, start=2):
        try:
            phone = clean_phone(get_flexible(row, 'phone', 'contact_no', 'phone_number', 'mobile'))
            name = get_flexible(row, 'name', 'customer_name', 'lead_name')
            
            detected_bootcamp = detect_bootcamp_from_row(row)
            bootcamp_title = (
                admin_bootcamp
                or get_flexible(
                    row,
                    'bootcamptitle', 'bootcamp_title', 'bootcamp',
                    'asset_name', 'opportunity_event_name', 'masterclass_name',
                )
                or get_flexible(row, 'opportunity_name', 'opportunity_event')
                or detected_bootcamp
                or name
                or 'Unknown Bootcamp'
            )

            # Course level & FP tier
            course_level = get_flexible(row, 'courselevel', 'course_level', 'lead_type')
            opp_name     = get_flexible(row, 'opportunity_name')
            selected_campaign = extra.get('campaign_type', 'fp_l1')
            fp_level     = detect_fp_level(course_level, opp_name or name, default_level=selected_campaign)

            if not phone:
                errors.append({'row': i, 'error': 'Missing phone', 'data': dict(row)})
                continue

            unique_key = make_unique_key(phone, bootcamp_title)

            def to_float(val):
                try:
                    return float(re.sub(r'[^0-9.]', '', val)) or None
                except Exception:
                    return None

            bootcamp_date_str = get_flexible(row, 'fpdate', 'fp_date', 'start_date', 'dummy_date_time', 'created_on', 'date')
            bootcamp_date = parse_date(bootcamp_date_str) or admin_date or bootcamp_date_str or None
            bootcamp_time_str = get_flexible(row, 'fptime', 'fp_time', 'start_time', 'time')
            bootcamp_time = parse_time(bootcamp_time_str) or parse_time(admin_time) or None

            lead = {
                'unique_key':          unique_key,
                'campaign_type':       fp_level,
                'lead_type':           course_level or opp_name or ('FP_L1' if fp_level == 'fp_l1' else 'FP_L2'),
                'lead_name':           name,
                'contact_no':          phone,
                'bootcamp_title':      bootcamp_title,
                'email':               get_flexible(row, 'email', 'email_address'),
                'amount':              to_float(get_flexible(row, 'amount', 'final_amount', 'quoted_amount', 'bootcamp_price')),
                'payment_status':      get_flexible(row, 'status'),
                'course_level':        course_level,
                'comment':             get_flexible(row, 'comment', 'comments', 'notes'),
                'coupon_code':         get_flexible(row, 'couponcode', 'coupon_code'),
                'payment_method_type': get_flexible(row, 'paymentmethodtype', 'payment_method_type', 'payment_type'),
                'fp_date':             bootcamp_date,
                'bootcamp_date':       bootcamp_date,
                'fp_time':             bootcamp_time,
                'agent_name':          get_flexible(row, 'agent', 'owner_user_name', 'owner_user_id', 'owner'),
                'priority':            admin_priority or get_flexible(row, 'priority', 'follow_up_priority') or None,
                'final_status':        'Pending',
                'uploaded_by':         uploaded_by,
                'upload_batch':        batch_id,
                'raw_data':            {k.strip(): v for k, v in row.items() if k},
            }
            leads.append(lead)
        except Exception as e:
            errors.append({'row': i, 'error': str(e), 'data': dict(row)})

    return leads, errors


# ============================================================
# PARSER: LEADSQUARED CRM EXPORT

# ============================================================
# Columns: Email Address, Phone Number, Lead Name, Opportunity Name,
# Category, Course Level, Owner_User Name_, Status, Stage, Comment,
# Asset Name, Start Date, Amount, Quoted Amount, Final Amount,
# Payment Type, Discount Added, Coupon Code, Received Amount,
# Balance Amount, EMI Per Month, Bootcamp Id, Bootcamp Price, ...

def _detect_campaign_from_opportunity(opp_name: str, course_level: str, category: str) -> str:
    """
    Auto-detect campaign_type from the Opportunity Name or Course Level field.
    Examples:
      FP_JUN03_L1_HIGH  -> fp_l1
      FP_JUN03_L1_LOW   -> fp_l2
      FP_JUN03_L2       -> fp_l2
      UPSELL_*          -> upsell
      SIA_* / STA_*     -> atpitch_sia / atpitch_sta
    """
    opp  = (opp_name or "").upper()
    cl   = (course_level or "").upper()
    cat  = (category or "").lower()

    # Failed Pending detection
    if opp.startswith("FP") or "FAILED" in opp or "PENDING" in opp:
        return detect_fp_level(cl, opp)

    # Upsell detection
    if "UPSELL" in opp or "UP_SELL" in opp:
        return "upsell"

    # Atpitch detection via Opportunity Name
    if "SIA" in opp or "SUPER INVEST" in opp:
        return "atpitch_sia"
    if "STA" in opp or "STOCK TRAD" in opp:
        return "atpitch_sta"

    # Fallback: try to detect from category
    if any(k in cat for k in ["investing", "stock", "trading", "finance"]):
        return detect_lead_type_atpitch(cat)

    return "atpitch_others"


def parse_leadsquared(
    file_content: bytes,
    uploaded_by: str = None,
    batch_id: str = None,
    extra: dict = None,
) -> tuple[list, list]:
    """
    Parse LeadSquared CRM export CSV.
    Auto-categorizes into the correct campaign_type.
    """
    extra = extra or {}
    admin_date     = clean_cell_value(extra.get('extra_date', ''))
    admin_priority = clean_cell_value(extra.get('extra_priority', ''))
    admin_bootcamp = clean_cell_value(extra.get('extra_bootcamp', ''))
    admin_time     = clean_cell_value(extra.get('extra_time', ''))

    delimiter = detect_delimiter(file_content)
    rows = read_csv_text(file_content, delimiter)
    leads = []
    errors = []

    for i, row in enumerate(rows, start=2):
        try:
            # ── Core identity fields ──────────────────────────────────────
            phone      = clean_phone(get_flexible(row, 'Phone Number', 'Phone', 'Mobile'))
            email      = get_flexible(row, 'Email Address', 'Email')
            name       = get_flexible(row, 'Name', 'Customer Name', 'Lead Name')

            if not phone and not email:
                errors.append({"row": i, "error": "Missing phone and email", "data": dict(row)})
                continue

            # ── Opportunity / Bootcamp fields ─────────────────────────────
            opp_name    = get_flexible(row, 'Opportunity Name')           # e.g. FP_JUN03_L1_HIGH
            asset_name  = get_flexible(row, 'Asset Name', 'Opportunity Event Name', 'MasterClass Name')
            
            detected_bootcamp = detect_bootcamp_from_row(row)
            bootcamp_title = (
                admin_bootcamp
                or asset_name
                or opp_name
                or detected_bootcamp
                or name
                or 'Unknown Bootcamp'
            )

            # ── Campaign auto-detection ───────────────────────────────────
            course_level  = get_flexible(row, 'Course Level')
            category      = get_flexible(row, 'Category')
            campaign_type = _detect_campaign_from_opportunity(opp_name, course_level, category)

            # ── Amounts ──────────────────────────────────────────────────
            def to_float(val):
                try:
                    return float(re.sub(r'[^0-9.]', '', val)) or None
                except Exception:
                    return None

            amount          = to_float(get_flexible(row, 'Final Amount', 'Quoted Amount', 'Amount'))
            bootcamp_price  = to_float(get_flexible(row, 'Bootcamp Price'))
            received_amount = to_float(get_flexible(row, 'Received Amount', 'Total Received Amount'))
            balance_amount  = to_float(get_flexible(row, 'Balance Amount'))
            discount_amount = to_float(get_flexible(row, 'Discount Added'))
            emi_per_month   = to_float(get_flexible(row, 'EMI Per Month'))

            # ── Dates ────────────────────────────────────────────────────
            bootcamp_date_str = get_flexible(row, 'Start Date', 'Dummy Date Time', 'Created On', 'Date')
            start_date = parse_date(bootcamp_date_str) or admin_date or bootcamp_date_str or None
            end_date   = parse_date(get_flexible(row, 'End Date'))
            next_fu    = parse_date(get_flexible(row, 'Next Follow Up'))
            bootcamp_time_str = get_flexible(row, 'fptime', 'fp_time', 'start_time', 'time')
            bootcamp_time = parse_time(bootcamp_time_str) or parse_time(admin_time) or None

            # ── Agent / Owner ─────────────────────────────────────────────
            agent_name = get_flexible(row, 'Owner User Name', 'Owner', 'Agent')

            # ── Status mapping ────────────────────────────────────────────
            ls_status  = get_flexible(row, 'Status')    # e.g. Open, Won, Lost
            stage      = get_flexible(row, 'Stage')     # e.g. website, token, enrolled
            payment_type = get_flexible(row, 'Payment Type')  # e.g. token, full, emi

            # Map LeadSquared status to internal final_status
            status_map = {
                'won':       'Converted',
                'lost':      'Not Interested',
                'enrolled':  'Already Enrolled',
                'open':      'Pending',
                'follow up': 'Follow Up',
                'followup':  'Follow Up',
            }
            final_status = status_map.get(
                (stage or ls_status or "").lower().strip(),
                'Pending'
            )
            if stage and stage.lower() in ['token', 'full payment', 'enrolled']:
                final_status = 'Converted'

            unique_key = make_unique_key(phone or email, bootcamp_title)

            lead = {
                "unique_key":          unique_key,
                "campaign_type":       campaign_type,
                "lead_type":           opp_name or course_level or campaign_type,
                "lead_name":           name,
                "contact_no":          phone or email,   # fallback to email if no phone
                "bootcamp_title":      bootcamp_title,
                "bootcamp_date":       start_date,
                "email":               email,
                "amount":              amount or bootcamp_price,
                "payment_status":      ls_status,
                "course_level":        course_level,
                "comment":             get_flexible(row, 'Comment', 'Notes', 'Description'),
                "coupon_code":         get_flexible(row, 'Coupon Code'),
                "payment_method_type": payment_type or get_flexible(row, 'Payment Mode'),
                "fp_date":             start_date,
                "fp_time":             bootcamp_time,
                "agent_name":          agent_name,
                "priority":            admin_priority or get_flexible(row, 'Follow Up Priority') or None,
                "final_status":        final_status,
                "uploaded_by":         uploaded_by,
                "upload_batch":        batch_id,
                # Store extra LS fields in raw_data
                "raw_data": {
                    **{k.strip(): v for k, v in row.items() if k},
                    "_ls_received_amount":  received_amount,
                    "_ls_balance_amount":   balance_amount,
                    "_ls_discount":         discount_amount,
                    "_ls_emi_per_month":    emi_per_month,
                    "_ls_end_date":         end_date,
                    "_ls_next_followup":    next_fu,
                    "_ls_bootcamp_id":      get_flexible(row, 'Bootcamp Id'),
                    "_ls_bootcamp_price":   bootcamp_price,
                    "_ls_opportunity_id":   get_flexible(row, 'Opportunity Id'),
                    "_ls_stage":            stage,
                },
            }
            leads.append(lead)

        except Exception as e:
            errors.append({"row": i, "error": str(e), "data": dict(row)})

    return leads, errors


# ============================================================
# PARSER: SIMPLE FORMAT  (phone + "name | BootcampTitle")
# ============================================================
# Two formats supported:
#
#  Format A — header has bootcamp in col-B name:
#    phone  |  name | Atpitch_Master Numerology_June9
#    9877226441  |  Onkar S Dharwal | Atpitch_Master Numerology_June9
#
#  Format B — generic header:
#    phone  |  name | bootcampTitle
#    9466650584  |  राजेश | Master Lal Kitab with Mahesh Mankar
#
# Admin must supply: Date, Priority  (via upload form extra fields)
# Campaign type is auto-detected from the bootcamp name.

def parse_simple(
    file_content: bytes,
    uploaded_by: str = None,
    batch_id: str = None,
    extra: dict = None,
) -> tuple[list, list]:
    """
    Parse simple two-column CSV: phone + "name | BootcampTitle".
    The bootcamp title is extracted by splitting on ` | `.
    Admin-supplied date and priority are applied to all rows.
    """
    extra = extra or {}
    admin_date     = extra.get('extra_date', '').strip()
    admin_priority = extra.get('extra_priority', '').strip()
    admin_bootcamp = extra.get('extra_bootcamp', '').strip()  # optional override
    admin_time     = extra.get('extra_time', '').strip()

    delimiter = detect_delimiter(file_content)
    text = file_content.decode('utf-8-sig', errors='replace')
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)

    rows = list(reader)
    if not rows:
        return [], [{"row": 0, "error": "Empty file"}]

    # ── Detect header row and extract bootcamp from column-B header ────────
    header = [h.strip() for h in rows[0]]
    header_bootcamp = ""
    if len(header) >= 2:
        # Column B header is like "name | Atpitch_Master Numerology_June9"
        # or "name | bootcampTitle"
        parts = header[1].split(' | ', 1)
        if len(parts) == 2 and parts[1].lower() not in ['bootcamptitle', 'bootcamp', 'bootcamp title', '']:
            header_bootcamp = parts[1].strip()

    data_rows = rows[1:]  # skip header
    leads = []
    errors = []

    for i, row in enumerate(data_rows, start=2):
        try:
            if not row or all(c.strip() == '' for c in row):
                continue  # skip empty rows

            # ── Phone (col A) ─────────────────────────────────────────────
            phone = clean_phone(row[0].strip() if len(row) > 0 else '')
            if not phone:
                errors.append({"row": i, "error": "Missing phone", "data": row})
                continue

            # ── Name + Bootcamp (col B: "name | BootcampTitle") ──────────
            col_b = row[1].strip() if len(row) > 1 else ''
            if ' | ' in col_b:
                parts = col_b.split(' | ', 1)
                lead_name     = clean_cell_value(parts[0])
                bootcamp_from_row = clean_cell_value(parts[1])
            else:
                # No separator — the whole column is the name
                lead_name         = clean_cell_value(col_b)
                bootcamp_from_row = ''

            # Priority: use admin-supplied value
            # Bootcamp: row value > admin override > header bootcamp
            bootcamp_title = (
                bootcamp_from_row
                or admin_bootcamp
                or header_bootcamp
                or 'Unknown Bootcamp'
            )

            # ── Auto-detect campaign from bootcamp name ───────────────────
            campaign_type = detect_lead_type_atpitch(bootcamp_title)

            unique_key = make_unique_key(phone, bootcamp_title)
            bootcamp_time = parse_time(admin_time) or None

            lead = {
                'unique_key':    unique_key,
                'campaign_type': campaign_type,
                'lead_type':     campaign_type.upper().replace('ATPITCH_', ''),
                'lead_name':     lead_name,
                'contact_no':    phone,
                'bootcamp_title': bootcamp_title,
                'bootcamp_date': admin_date or None,
                'fp_time':       bootcamp_time,
                'priority':      admin_priority or None,
                'final_status':  'Pending',
                'uploaded_by':   uploaded_by,
                'upload_batch':  batch_id,
                'raw_data':      {'phone': phone, 'raw_name_col': col_b,
                                   'admin_date': admin_date, 'admin_priority': admin_priority, 'admin_time': admin_time},
            }
            leads.append(lead)

        except Exception as e:
            errors.append({"row": i, "error": str(e), "data": row})

    return leads, errors


# ============================================================
# DISPATCH
# ============================================================

PARSERS = {
    'atpitch_sia':    parse_atpitch,
    'atpitch_sta':    parse_atpitch,
    'atpitch_others': parse_atpitch,
    'upsell':         parse_upsell,
    'fp_l1':          parse_failed_pending,
    'fp_l2':          parse_failed_pending,
    'leadsquared':    parse_leadsquared,
    'simple':         parse_simple,          # ← two-column: phone + "name | bootcamp"
}


def parse_file(
    campaign_type: str,
    file_content: bytes,
    uploaded_by: str = None,
    batch_id: str = None,
    extra: dict = None,
) -> tuple[list, list]:
    """Main dispatch — routes to the correct parser."""
    parser = PARSERS.get(campaign_type)
    if not parser:
        raise ValueError(f"Unknown campaign type: {campaign_type}")
    # All parsers accept extra kwarg; older ones ignore it
    try:
        return parser(file_content, uploaded_by=uploaded_by, batch_id=batch_id, extra=extra or {})
    except TypeError:
        # Older parser signature without extra
        return parser(file_content, uploaded_by=uploaded_by, batch_id=batch_id)
