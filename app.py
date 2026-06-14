"""
TFU LeadSquared CRM — Flask Application
All routes, authentication, and API endpoints.
"""
import os
import json
import uuid
import httpx
from pathlib import Path
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, g
)
from dotenv import load_dotenv

# Always load .env from the same directory as this file
_BASE_DIR = Path(__file__).parent
load_dotenv(_BASE_DIR / '.env')

from supabase_client import supabase, supabase_admin
from parsers import parse_file

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload


# ============================================================
# AUTH HELPERS
# ============================================================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to continue.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Admin access required.', 'error')
            return redirect(url_for('agent_dashboard'))
        return f(*args, **kwargs)
    return decorated


def get_current_user():
    if 'user_id' in session:
        return {
            'id': session['user_id'],
            'name': session.get('name', ''),
            'email': session.get('email', ''),
            'role': session.get('role', 'agent'),
        }
    return None


# ============================================================
# AGENT CAMPAIGN TEAMS AND MAPPINGS
# ============================================================

SIA_STA_TEAM = {'harsh', 'krishna', 'deepak', 'manmohan', 'kamaljeet'}
FP_TEAM      = {'akansha', 'kulbir', 'abhisekh', 'faiz ansari'}
UPSELL_TEAM  = {'anchal', 'muskan', 'khusbu', 'sumaitari', 'sumaitri', 'ameen', 'pankaj', 'jyoti'}

def agent_matches_team(db_name: str, team_set: set) -> bool:
    if not db_name:
        return False
    name_clean = db_name.strip().lower()
    if name_clean in team_set:
        return True
    first_name = name_clean.split()[0]
    if first_name in team_set:
        return True
    for t in team_set:
        if t in name_clean:
            return True
    return False

def get_agent_allowed_campaigns(agent_name: str) -> list:
    allowed = []
    if agent_matches_team(agent_name, SIA_STA_TEAM):
        allowed.extend(['atpitch_sia', 'atpitch_sta'])
    if agent_matches_team(agent_name, FP_TEAM):
        allowed.extend(['fp_l1'])
    if agent_matches_team(agent_name, UPSELL_TEAM):
        allowed.extend(['upsell', 'atpitch_others'])
    return allowed


@app.context_processor
def inject_allowed_campaigns():
    user = get_current_user()
    if not user:
        return {'allowed_campaigns': []}
    if user['role'] == 'admin':
        return {'allowed_campaigns': ['atpitch_sia', 'atpitch_sta', 'atpitch_others', 'upsell', 'fp_l1']}
    return {'allowed_campaigns': get_agent_allowed_campaigns(user['name'])}


# ============================================================
# AUTH ROUTES
# ============================================================

@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('agent_dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        try:
            supabase_url = os.environ.get('SUPABASE_URL', '')
            anon_key     = os.environ.get('SUPABASE_ANON_KEY', '')  # ← correct key

            # Call Supabase Auth directly via httpx with a 30-second timeout
            auth_r = httpx.post(
                f"{supabase_url}/auth/v1/token?grant_type=password",
                headers={
                    'apikey':       anon_key,
                    'Content-Type': 'application/json',
                },
                json={'email': email, 'password': password},
                timeout=30.0,
            )

            if auth_r.status_code != 200:
                err = auth_r.json().get('error_description',
                      auth_r.json().get('msg', 'Login failed'))
                flash('Invalid email or password.' if 'invalid' in str(err).lower()
                      else f'Login failed: {err}', 'error')
                return render_template('login.html')

            auth_data    = auth_r.json()
            user_id      = auth_data['user']['id']
            access_token = auth_data['access_token']

            # Fetch profile
            profile_resp = supabase_admin.table('profiles').select('*').eq('id', user_id).single().execute()
            profile = profile_resp.data

            if not profile:
                flash('Account not set up. Contact admin.', 'error')
                return render_template('login.html')

            if not profile.get('is_active', True):
                flash('Account is deactivated. Contact admin.', 'error')
                return render_template('login.html')

            # Store in session
            session['user_id']      = user_id
            session['email']        = email
            session['name']         = profile.get('name', email)
            session['role']         = profile.get('role', 'agent')
            session['access_token'] = access_token

            flash(f'Welcome back, {profile.get("name")}! 👋', 'success')

            if profile.get('role') == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('agent_dashboard'))

        except httpx.TimeoutException:
            flash('Connection timed out. Check your internet and try again.', 'error')
        except httpx.ConnectError:
            flash('Cannot reach Supabase. Check your network connection.', 'error')
        except Exception as e:
            flash(f'Login error: {str(e)}', 'error')

    return render_template('login.html')


@app.route('/logout')
def logout():
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('login'))


# ============================================================
# ADMIN ROUTES
# ============================================================

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    user = get_current_user()
    today = datetime.now(timezone.utc).date().isoformat()

    # ── Defaults ─────────────────────────────────────────────────────────
    stats  = {'total_leads': 0, 'converted': 0, 'follow_up': 0,
              'today_calls': 0, 'campaign_stats': {}}
    detail = {'converted_leads': [], 'follow_up_leads': [], 'today_call_logs': []}
    recent_uploads = []

    # ── Query 1: counts in parallel using thread pool ─────────────────────
    try:
        from concurrent.futures import ThreadPoolExecutor
        campaign_types = ['atpitch_sia','atpitch_sta','atpitch_others','upsell','fp_l1']

        queries = {
            'total': supabase_admin.table('leads').select('id', count='exact'),
            'converted': supabase_admin.table('leads').select('id', count='exact').eq('final_status', 'Converted'),
            'follow_up': supabase_admin.table('leads').select('id', count='exact').eq('final_status', 'Follow Up'),
        }
        for ct in campaign_types:
            queries[f'camp_{ct}'] = supabase_admin.table('leads').select('id', count='exact').eq('campaign_type', ct)

        def get_count_val(q):
            return q.execute().count or 0

        counts = {}
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                future_to_key = {executor.submit(get_count_val, q): key for key, q in queries.items()}
                for future in future_to_key:
                    key = future_to_key[future]
                    counts[key] = future.result()
        except Exception as tpe_err:
            app.logger.warning(f"ThreadPoolExecutor failed in admin_dashboard, falling back to sequential execution: {tpe_err}")
            counts = {}
            for key, q in queries.items():
                try:
                    counts[key] = get_count_val(q)
                except Exception as seq_err:
                    counts[key] = 0
                    app.logger.error(f"Sequential fallback query failed for key {key}: {seq_err}")

        total = counts.get('total', 0)
        n_conv = counts.get('converted', 0)
        n_fu = counts.get('follow_up', 0)
        campaign_stats = {ct: counts.get(f'camp_{ct}', 0) for ct in campaign_types}

        # ── Query 2: Retrieve detail lists ────────────────────────────────────
        # Fetch latest 50 converted leads
        converted_leads_resp = supabase_admin.table('leads') \
            .select('id,lead_name,contact_no,bootcamp_title,campaign_type,'
                    'priority,agent_name,final_status,last_call_date,updated_at') \
            .eq('final_status', 'Converted') \
            .order('updated_at', desc=True) \
            .limit(50) \
            .execute()
        converted_leads = converted_leads_resp.data or []

        # Fetch up to 1000 follow-up leads (to search for overdue status)
        follow_up_all_resp = supabase_admin.table('leads') \
            .select('id,lead_name,contact_no,bootcamp_title,campaign_type,'
                    'priority,agent_name,final_status,last_call_date,updated_at') \
            .eq('final_status', 'Follow Up') \
            .limit(1000) \
            .execute()
        follow_up_all = follow_up_all_resp.data or []

        # Latest 50 follow ups for the Follow-Up Leads panel
        follow_up_leads = sorted(follow_up_all,
                                 key=lambda x: x.get('last_call_date') or '', reverse=True)[:50]

        # Calculate overdue follow-ups
        fu_lead_ids = [l['id'] for l in follow_up_all]
        followup_info = {}
        if fu_lead_ids:
            attempts_data = []
            # Query call attempts in chunks of 200 to prevent long URL query failures
            for i in range(0, len(fu_lead_ids), 200):
                chunk = fu_lead_ids[i:i+200]
                attempts_resp = supabase_admin.table('call_attempts')\
                    .select('lead_id,follow_up_date,follow_up_time')\
                    .in_('lead_id', chunk)\
                    .eq('call_status', 'follow_up')\
                    .order('called_at', desc=True)\
                    .execute()
                attempts_data.extend(attempts_resp.data or [])

            for att in attempts_data:
                l_id = att.get('lead_id')
                if l_id not in followup_info:
                    followup_info[l_id] = {
                        'date': att.get('follow_up_date'),
                        'time': att.get('follow_up_time')
                    }

        from datetime import timezone as py_timezone, timedelta as py_timedelta
        ist = py_timezone(py_timedelta(hours=5, minutes=30))
        now_local = datetime.now(py_timezone.utc).astimezone(ist)

        overdue_leads = []
        for lead in follow_up_all:
            info = followup_info.get(lead['id'], {})
            lead_date = info.get('date') or lead.get('fp_date')
            lead_time = info.get('time') or lead.get('fp_time')
            lead['follow_up_date'] = lead_date
            lead['follow_up_time'] = lead_time
            if lead_date:
                try:
                    t_str = lead_time if lead_time else "00:00:00"
                    if len(t_str) == 5:
                        t_str = f"{t_str}:00"
                    scheduled_dt = datetime.strptime(f"{lead_date} {t_str}", "%Y-%m-%d %H:%M:%S")
                    scheduled_dt = scheduled_dt.replace(tzinfo=ist)
                    if now_local > scheduled_dt + py_timedelta(hours=24):
                        diff = now_local - scheduled_dt
                        lead['hours_overdue'] = int(diff.total_seconds() // 3600)
                        overdue_leads.append(lead)
                except Exception as ex:
                    app.logger.warning(f"Error parsing follow_up_date/time for lead {lead['id']}: {ex}")

        overdue_leads = sorted(overdue_leads, key=lambda x: x.get('hours_overdue', 0), reverse=True)

        stats.update({
            'total_leads':       total,
            'converted':         n_conv,
            'follow_up':         n_fu,
            'overdue_followups': len(overdue_leads),
            'campaign_stats':    campaign_stats,
        })
        detail['converted_leads'] = converted_leads
        detail['follow_up_leads'] = follow_up_leads
        detail['overdue_followups'] = overdue_leads[:50]

    except Exception as e:
        flash(f'Could not load leads: {e}', 'error')

    # ── Query 2: today's call count ────────────────────────────────────────
    try:
        tc = supabase_admin.table('call_attempts') \
            .select('id', count='exact') \
            .gte('called_at', today).execute()
        stats['today_calls'] = tc.count or 0
    except Exception:
        stats['today_calls'] = 0

    # ── Query 3: today's call detail (for drilldown) ──────────────────────
    try:
        tcl = supabase_admin.table('call_attempts') \
            .select('id,called_at,call_status,leads(lead_name,contact_no,bootcamp_title,campaign_type)') \
            .gte('called_at', today) \
            .order('called_at', desc=True).limit(50).execute()
        detail['today_call_logs'] = tcl.data or []
    except Exception:
        detail['today_call_logs'] = []

    # ── Query 4: recent uploads ────────────────────────────────────────────
    try:
        ru = supabase_admin.table('upload_logs') \
            .select('*').order('created_at', desc=True).limit(10).execute()
        recent_uploads = ru.data or []
    except Exception:
        recent_uploads = []

    # ── Query 5: active agents list (for dashboard assignment) ───────────────
    try:
        agents_resp = supabase_admin.table('profiles').select('id,name').eq('role', 'agent').eq('is_active', True).execute()
        agents = agents_resp.data or []
    except Exception:
        agents = []

    return render_template('admin/dashboard.html',
                           user=user,
                           stats=stats,
                           detail=detail,
                           recent_uploads=recent_uploads,
                           agents=agents)


@app.route('/admin/dashboard/export_csv')
@admin_required
def admin_export_report():
    import csv
    import io
    from flask import Response

    all_leads = []
    limit = 1000
    offset = 0
    
    try:
        while True:
            # We want to select all lead columns AND their related call attempts.
            # Supabase Postgrest syntax: leads(*, call_attempts(*))
            res = supabase_admin.table('leads')\
                .select('*, call_attempts(*)')\
                .range(offset, offset + limit - 1)\
                .execute()
            
            batch = res.data or []
            if not batch:
                break
            all_leads.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
    except Exception as e:
        flash(f'Failed to fetch leads for report: {e}', 'error')
        return redirect(url_for('admin_dashboard'))

    # Generate CSV in memory
    si = io.StringIO()
    cw = csv.writer(si)
    
    # Header row
    cw.writerow([
        'Lead ID', 'Lead Name', 'Phone', 'Email', 'Campaign Type',
        'Bootcamp Title', 'Bootcamp Date', 'Priority', 'Assigned Agent',
        'Final Status', 'Total Attempts (Calls)', 'Last Called At', 'Contacted By Agent',
        'Original Upload Amount', 'Original Payment Status', 'Original Payment Mode', 'Original Coupon Code',
        'Amount Paid on Conversion', 'Token Amount', 'Discount Amount', 'Bootcamp Price (Conversion)',
        'Payment Mode on Conversion', 'Payment Reference', 'Call History Summary'
    ])
    
    for lead in all_leads:
        attempts = lead.get('call_attempts') or []
        # Sort attempts by attempt_number or called_at
        attempts = sorted(attempts, key=lambda x: x.get('attempt_number', 0))
        
        total_calls = len(attempts)
        last_called_at = lead.get('last_call_date') or ''
        
        # Payment details on conversion
        conv_amount_paid = ''
        conv_token_amount = ''
        conv_discount_amount = ''
        conv_bootcamp_price = ''
        conv_payment_mode = ''
        conv_payment_ref = ''
        
        # Look for the conversion attempt
        for att in attempts:
            if att.get('call_status') == 'converted':
                conv_amount_paid = att.get('amount_paid') or ''
                conv_token_amount = att.get('token_amount') or ''
                conv_discount_amount = att.get('discount_amount') or ''
                conv_bootcamp_price = att.get('bootcamp_price') or ''
                conv_payment_mode = att.get('payment_mode') or ''
                conv_payment_ref = att.get('payment_reference') or ''
                break
                
        # Call history summary text
        call_history_parts = []
        for att in attempts:
            num = att.get('attempt_number', 1)
            agent = att.get('agent_name') or 'unknown agent'
            dt = att.get('called_at') or ''
            # Format date string for readability
            if dt:
                try:
                    dt = datetime.fromisoformat(dt).strftime('%Y-%m-%d %H:%M')
                except Exception:
                    pass
            
            connected = att.get('connected')
            status_desc = ''
            if connected:
                status_desc = f"Connected ({att.get('call_status') or ''})"
            else:
                status_desc = f"Not Connected ({att.get('not_connected_reason') or ''})"
                
            comments = att.get('comments') or ''
            comment_str = f" - Comments: {comments}" if comments else ""
            call_history_parts.append(f"Attempt {num}: {status_desc} by {agent} on {dt}{comment_str}")
            
        history_summary = " | ".join(call_history_parts)
        
        cw.writerow([
            lead.get('id'),
            lead.get('lead_name') or '',
            lead.get('contact_no') or '',
            lead.get('email') or '',
            lead.get('campaign_type') or '',
            lead.get('bootcamp_title') or '',
            lead.get('bootcamp_date') or '',
            lead.get('priority') or '',
            lead.get('agent_name') or '',
            lead.get('final_status') or '',
            total_calls,
            last_called_at,
            lead.get('contacted_by') or '',
            lead.get('amount') or '',
            lead.get('payment_status') or '',
            lead.get('payment_method_type') or '',
            lead.get('coupon_code') or '',
            conv_amount_paid,
            conv_token_amount,
            conv_discount_amount,
            conv_bootcamp_price,
            conv_payment_mode,
            conv_payment_ref,
            history_summary
        ])
        
    response = Response(si.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=tfu_detailed_report.csv'
    return response



@app.route('/admin/upload', methods=['GET', 'POST'])
@admin_required
def admin_upload():
    user = get_current_user()
    if request.method == 'POST':
        campaign_type = request.form.get('campaign_type', '')
        file = request.files.get('file')
        agent_names = [a.strip() for a in request.form.getlist('agent_names') if a.strip()]

        if not file or file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400

        if not campaign_type:
            return jsonify({'success': False, 'error': 'No campaign type selected'}), 400

        allowed_ext = {'.csv', '.tsv', '.txt'}
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in allowed_ext:
            return jsonify({'success': False, 'error': f'File type {ext} not supported. Use CSV.'}), 400

        file_content = file.read()
        batch_id = datetime.now(timezone.utc).isoformat()

        # Extra metadata for simple-format uploads
        extra_meta = {
            'extra_date':     request.form.get('extra_date', '').strip(),
            'extra_priority': request.form.get('extra_priority', '').strip(),
            'extra_bootcamp': request.form.get('extra_bootcamp', '').strip(),
            'extra_time':     request.form.get('extra_time', '').strip(),
            'campaign_type':  campaign_type,
        }

        try:
            leads, errors = parse_file(campaign_type, file_content,
                                        uploaded_by=user['id'], batch_id=batch_id,
                                        extra=extra_meta)
            # If no specific agents selected, dynamically assign based on each lead's campaign type
            if not agent_names:
                try:
                    agents_resp = supabase_admin.table('profiles')\
                        .select('name')\
                        .eq('role', 'agent')\
                        .eq('is_active', True)\
                        .execute()
                    db_active_agents = [a['name'].strip() for a in agents_resp.data or [] if a.get('name')]
                except Exception:
                    db_active_agents = []

                # Group active database agents by their campaign teams using clean substring / first-name checks
                active_sia_sta = [a for a in db_active_agents if agent_matches_team(a, SIA_STA_TEAM)]
                active_fp      = [a for a in db_active_agents if agent_matches_team(a, FP_TEAM)]
                active_upsell  = [a for a in db_active_agents if agent_matches_team(a, UPSELL_TEAM)]

                for lead in leads:
                    if not lead.get('agent_name'):
                        ct = lead.get('campaign_type') or campaign_type
                        if ct in ['atpitch_sia', 'atpitch_sta']:
                            team_agents = active_sia_sta
                        elif ct == 'fp_l1':
                            team_agents = active_fp
                        elif ct in ['upsell', 'atpitch_others']:
                            team_agents = active_upsell
                        else:
                            team_agents = db_active_agents
                        
                        if team_agents:
                            lead['agent_name'] = ", ".join(team_agents)

            if agent_names:
                all_agents_str = ", ".join(agent_names)
                for lead in leads:
                    if not lead.get('agent_name'):
                        lead['agent_name'] = all_agents_str
        except Exception as e:
            return jsonify({'success': False, 'error': f'Parse error: {str(e)}'}), 500

        inserted = 0
        duplicates = 0
        insert_errors = []

        # ── Batch upsert in chunks of 500 rows ──────────────────────────────
        # Uses on_conflict=ignore so duplicates are silently skipped (no exceptions)
        # This reduces N network calls → ceil(N/500) calls — 50-200x faster
        BATCH_SIZE = 500
        for i in range(0, len(leads), BATCH_SIZE):
            batch = leads[i : i + BATCH_SIZE]
            try:
                result = supabase_admin.table('leads').upsert(
                    batch,
                    on_conflict='unique_key',   # skip rows where unique_key already exists
                    ignore_duplicates=True,
                ).execute()
                # Count how many were actually inserted vs skipped
                returned = len(result.data) if result.data else 0
                skipped  = len(batch) - returned
                inserted   += returned
                duplicates += skipped
            except Exception as e:
                err_str = str(e)
                # If whole batch fails, fall back to individual inserts for this batch
                for lead in batch:
                    try:
                        supabase_admin.table('leads').insert(lead).execute()
                        inserted += 1
                    except Exception as e2:
                        e2_str = str(e2)
                        if 'unique' in e2_str.lower() or 'duplicate' in e2_str.lower():
                            duplicates += 1
                        else:
                            insert_errors.append({
                                'unique_key': lead.get('unique_key'),
                                'error': e2_str[:200]
                            })

        # Log the upload
        try:
            supabase_admin.table('upload_logs').insert({
                'uploaded_by': user['id'],
                'campaign_type': campaign_type,
                'filename': file.filename,
                'total_rows': len(leads),
                'inserted_rows': inserted,
                'duplicate_rows': duplicates,
                'error_rows': len(insert_errors) + len(errors),
                'errors': errors[:50] + insert_errors[:50],
            }).execute()
        except Exception:
            pass

        return jsonify({
            'success': True,
            'total': len(leads),
            'inserted': inserted,
            'duplicates': duplicates,
            'parse_errors': len(errors),
            'insert_errors': len(insert_errors),
        })

    # Fetch active agents for assignment dropdown
    try:
        agents_resp = supabase_admin.table('profiles').select('id,name').eq('role', 'agent').eq('is_active', True).execute()
        agents = agents_resp.data or []
    except Exception:
        agents = []

    return render_template('admin/upload.html', user=user, agents=agents)


@app.route('/admin/leads')
@admin_required
def admin_leads():
    user = get_current_user()
    campaign_type = request.args.get('campaign_type', '')
    status = request.args.get('status', '')
    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    per_page = 50

    try:
        query = supabase_admin.table('leads').select('*')
        if campaign_type:
            query = query.eq('campaign_type', campaign_type)
        if status:
            query = query.eq('final_status', status)
        if search:
            query = query.or_(f'lead_name.ilike.%{search}%,contact_no.ilike.%{search}%,bootcamp_title.ilike.%{search}%')

        offset = (page - 1) * per_page
        result = query.order('updated_at', desc=True).range(offset, offset + per_page - 1).execute()
        leads = result.data or []

        # Count
        count_query = supabase_admin.table('leads').select('id', count='exact')
        if campaign_type:
            count_query = count_query.eq('campaign_type', campaign_type)
        if status:
            count_query = count_query.eq('final_status', status)
        if search:
            count_query = count_query.or_(f'lead_name.ilike.%{search}%,contact_no.ilike.%{search}%,bootcamp_title.ilike.%{search}%')
        count_result = count_query.execute()
        total = count_result.count or 0
    except Exception as e:
        leads = []
        total = 0
        flash(f'Error: {e}', 'error')

    # Fetch active agents for bulk assignment
    try:
        agents_resp = supabase_admin.table('profiles').select('id,name').eq('role', 'agent').eq('is_active', True).execute()
        agents = agents_resp.data or []
    except Exception:
        agents = []

    total_pages = (total + per_page - 1) // per_page

    return render_template('admin/leads.html',
                           user=user,
                           leads=leads,
                           agents=agents,
                           total=total,
                           page=page,
                           total_pages=total_pages,
                           campaign_type=campaign_type,
                           status=status,
                           search=search)


@app.route('/admin/leads/assign', methods=['POST'])
@admin_required
def admin_leads_assign():
    unassign_action = request.form.get('unassign_action') == 'yes'
    agent_names = [a.strip() for a in request.form.getlist('agent_names') if a.strip()]
    lead_ids_str = request.form.get('lead_ids', '').strip()
    
    if not lead_ids_str:
        flash('No leads selected for assignment.', 'error')
        return redirect(url_for('admin_leads'))
        
    lead_ids = [lid.strip() for lid in lead_ids_str.split(',') if lid.strip()]
    if not lead_ids:
        flash('No leads selected for assignment.', 'error')
        return redirect(url_for('admin_leads'))
        
    try:
        if unassign_action or not agent_names:
            supabase_admin.table('leads')\
                .update({'agent_name': ''})\
                .in_('id', lead_ids)\
                .execute()
            flash(f'Successfully unassigned {len(lead_ids)} leads.', 'success')
        else:
            # Assign all selected agents to all selected leads
            all_agents_str = ", ".join(agent_names)
            supabase_admin.table('leads')\
                .update({'agent_name': all_agents_str})\
                .in_('id', lead_ids)\
                .execute()
            flash(f'Successfully assigned {len(lead_ids)} leads to {all_agents_str}.', 'success')
    except Exception as e:
        flash(f'Failed to assign leads: {e}', 'error')
        
    return redirect(url_for('admin_leads', 
                           campaign_type=request.form.get('campaign_type', ''),
                           status=request.form.get('status', ''),
                           search=request.form.get('search', ''),
                           page=request.form.get('page', '1')))


@app.route('/admin/leads/<lead_id>/delete', methods=['POST'])
@admin_required
def admin_leads_delete(lead_id):
    try:
        supabase_admin.table('leads').delete().eq('id', lead_id).execute()
        flash('Lead successfully deleted.', 'success')
    except Exception as e:
        flash(f'Failed to delete lead: {e}', 'error')
    
    referrer = request.referrer or ''
    if '/agent/leads/' in referrer:
        return redirect(url_for('admin_leads'))
    if referrer:
        return redirect(referrer)
    return redirect(url_for('admin_leads'))


@app.route('/admin/leads/bulk-delete', methods=['POST'])
@admin_required
def admin_leads_bulk_delete():
    lead_ids_str = request.form.get('lead_ids', '').strip()
    if not lead_ids_str:
        flash('No leads selected for deletion.', 'error')
        return redirect(url_for('admin_leads'))
        
    lead_ids = [lid.strip() for lid in lead_ids_str.split(',') if lid.strip()]
    if not lead_ids:
        flash('No leads selected for deletion.', 'error')
        return redirect(url_for('admin_leads'))
        
    try:
        supabase_admin.table('leads').delete().in_('id', lead_ids).execute()
        flash(f'Successfully deleted {len(lead_ids)} leads.', 'success')
    except Exception as e:
        flash(f'Failed to delete leads: {e}', 'error')
        
    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    return redirect(url_for('admin_leads'))


@app.route('/admin/agents')
@admin_required
def admin_agents():
    user = get_current_user()
    try:
        agents = supabase_admin.table('profiles').select('*').eq('role', 'agent').order('name').execute()
        agents_data = agents.data or []
    except Exception as e:
        agents_data = []
        flash(f'Error: {e}', 'error')
    return render_template('admin/agents.html', user=user, agents=agents_data)


@app.route('/admin/agents/create', methods=['POST'])
@admin_required
def admin_create_agent():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '').strip()

    if not all([name, email, password]):
        flash('All fields are required.', 'error')
        return redirect(url_for('admin_agents'))

    try:
        # Create auth user with admin client
        auth_resp = supabase_admin.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True,
        })
        new_user = auth_resp.user

        # Insert profile
        supabase_admin.table('profiles').insert({
            'id': str(new_user.id),
            'name': name,
            'email': email,
            'role': 'agent',
            'password': password,
        }).execute()

        flash(f'Agent {name} created successfully! They can log in at /login with their email and password.', 'success')
    except Exception as e:
        flash(f'Error creating agent: {e}', 'error')

    return redirect(url_for('admin_agents'))


@app.route('/admin/agents/<agent_id>/toggle', methods=['POST'])
@admin_required
def admin_toggle_agent(agent_id):
    action = request.form.get('action', 'deactivate')
    is_active = (action == 'activate')
    try:
        supabase_admin.table('profiles') \
            .update({'is_active': is_active}) \
            .eq('id', agent_id).execute()
        flash(f'Agent {"activated" if is_active else "deactivated"} successfully.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(url_for('admin_agents'))


@app.route('/admin/agents/reset-password', methods=['POST'])
@admin_required
def admin_reset_agent_password():
    agent_id     = request.form.get('agent_id', '').strip()
    new_password = request.form.get('new_password', '').strip()
    if not agent_id or not new_password:
        flash('Agent ID and new password are required.', 'error')
        return redirect(url_for('admin_agents'))
    try:
        supabase_admin.auth.admin.update_user_by_id(
            agent_id,
            {'password': new_password}
        )
        supabase_admin.table('profiles').update({
            'password': new_password
        }).eq('id', agent_id).execute()
        flash('Password reset successfully. Share the new password with the agent.', 'success')
    except Exception as e:
        flash(f'Error resetting password: {e}', 'error')
    return redirect(url_for('admin_agents'))


# ============================================================
# AGENT ROUTES
# ============================================================

CAMPAIGN_LABELS = {
    'atpitch_sia': 'Atpitch SIA',
    'atpitch_sta': 'Atpitch STA',
    'atpitch_others': 'Atpitch Others',
    'upsell': 'Upsell OB',
    'fp_l1': 'FP OB Campaign',
}

CAMPAIGN_ICONS = {
    'atpitch_sia': '📈',
    'atpitch_sta': '📊',
    'atpitch_others': '🎯',
    'upsell': '⬆️',
    'fp_l1': '💳',
}

CAMPAIGN_COLORS = {
    'atpitch_sia': 'purple',
    'atpitch_sta': 'blue',
    'atpitch_others': 'teal',
    'upsell': 'orange',
    'fp_l1': 'green',
}


@app.route('/agent/dashboard')
@login_required
def agent_dashboard():
    user       = get_current_user()
    agent_name = user['name']
    is_admin   = user['role'] == 'admin'

    # Hardcoded campaign definitions — no dependency on module-level dicts
    _camps = [
        ('atpitch_sia',    'Atpitch SIA',       '📈', 'purple'),
        ('atpitch_sta',    'Atpitch STA',        '📊', 'blue'),
        ('atpitch_others', 'Atpitch Others',     '🎯', 'teal'),
        ('upsell',         'Upsell OB',          '⬆️', 'orange'),
        ('fp_l1',          'FP OB Campaign',     '💳', 'green'),
    ]
    if not is_admin:
        allowed = get_agent_allowed_campaigns(agent_name)
        _camps = [c for c in _camps if c[0] in allowed]
    _ctypes = [c[0] for c in _camps]

    # ── Parallel Supabase count queries ───────────────────────────────────
    _total = {c: 0 for c in _ctypes}
    _pend  = {c: 0 for c in _ctypes}
    _fu    = {c: 0 for c in _ctypes}

    try:
        from concurrent.futures import ThreadPoolExecutor

        queries = {}
        for ct in _ctypes:
            # Total
            q_tot = supabase_admin.table('leads').select('id', count='exact').eq('campaign_type', ct)
            if not is_admin:
                q_tot = q_tot.ilike('agent_name', f'%{agent_name}%').or_(f"final_status.neq.Follow Up,contacted_by.is.null,contacted_by.eq.{agent_name}")
            queries[f'{ct}_total'] = q_tot

            # Pending
            q_pend = supabase_admin.table('leads').select('id', count='exact').eq('campaign_type', ct).eq('final_status', 'Pending')
            if not is_admin:
                q_pend = q_pend.ilike('agent_name', f'%{agent_name}%')
            queries[f'{ct}_pending'] = q_pend

            # Follow Up
            q_fu = supabase_admin.table('leads').select('id', count='exact').eq('campaign_type', ct).eq('final_status', 'Follow Up')
            if not is_admin:
                q_fu = q_fu.ilike('agent_name', f'%{agent_name}%').or_(f"contacted_by.is.null,contacted_by.eq.{agent_name}")
            queries[f'{ct}_fu'] = q_fu

        def get_count_val(q):
            return q.execute().count or 0

        counts = {}
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                future_to_key = {executor.submit(get_count_val, q): key for key, q in queries.items()}
                for future in future_to_key:
                    key = future_to_key[future]
                    counts[key] = future.result()
        except Exception as tpe_err:
            app.logger.warning(f"ThreadPoolExecutor failed in agent_dashboard, falling back to sequential execution: {tpe_err}")
            counts = {}
            for key, q in queries.items():
                try:
                    counts[key] = get_count_val(q)
                except Exception as seq_err:
                    counts[key] = 0
                    app.logger.error(f"Sequential fallback query failed for key {key}: {seq_err}")

        for ct in _ctypes:
            _total[ct] = counts.get(f'{ct}_total', 0)
            _pend[ct]  = counts.get(f'{ct}_pending', 0)
            _fu[ct]    = counts.get(f'{ct}_fu', 0)

    except Exception as ex:
        app.logger.warning(f'agent_dashboard main query block failed: {ex}')

    # ── Build campaigns list ── guaranteed list of plain dicts ─────────────
    campaigns = []
    for (ct, lbl, icon, color) in _camps:
        d = {
            'type':      ct,
            'label':     lbl,
            'icon':      icon,
            'color':     color,
            'total':     _total[ct],
            'pending':   _pend[ct],
            'follow_up': _fu[ct],
        }
        campaigns.append(d)

    app.logger.info(f'[agent_dashboard] campaigns[0] = {campaigns[0] if campaigns else "empty"}')

    return render_template('agent/dashboard.html', user=user, campaigns=campaigns)


@app.route('/agent/campaigns/<campaign_type>')
@login_required
def agent_campaign(campaign_type):
    user = get_current_user()
    agent_name = user['name']
    is_admin = user['role'] == 'admin'

    if not is_admin:
        allowed = get_agent_allowed_campaigns(agent_name)
        if campaign_type not in allowed:
            flash('Access denied. You do not have access to this campaign.', 'error')
            return redirect(url_for('agent_dashboard'))

    status_filter = request.args.get('status', '')
    priority_filter = request.args.get('priority', '')
    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    per_page = 30

    try:
        query = supabase_admin.table('leads').select('*').eq('campaign_type', campaign_type)
        if not is_admin:
            query = query.ilike('agent_name', f'%{agent_name}%')
            query = query.or_(f"final_status.neq.Follow Up,contacted_by.is.null,contacted_by.eq.{agent_name}")
        if status_filter:
            query = query.eq('final_status', status_filter)
        if priority_filter:
            query = query.eq('priority', priority_filter)
        if search:
            query = query.or_(f'lead_name.ilike.%{search}%,contact_no.ilike.%{search}%,bootcamp_title.ilike.%{search}%')

        offset = (page - 1) * per_page
        result = query.order('updated_at', desc=True).range(offset, offset + per_page - 1).execute()
        leads = result.data or []
    except Exception as e:
        leads = []
        flash(f'Error: {e}', 'error')

    label = CAMPAIGN_LABELS.get(campaign_type, campaign_type)
    return render_template('agent/campaign.html',
                           user=user,
                           leads=leads,
                           campaign_type=campaign_type,
                           campaign_label=label,
                           status_filter=status_filter,
                           priority_filter=priority_filter,
                           search=search,
                           page=page)


@app.route('/agent/leads/<lead_id>')
@login_required
def agent_lead_detail(lead_id):
    user = get_current_user()
    try:
        lead_resp = supabase_admin.table('leads').select('*').eq('id', lead_id).single().execute()
        lead = lead_resp.data

        if not lead:
            flash('Lead not found.', 'error')
            return redirect(url_for('agent_dashboard'))

        # Enforce agent access rules
        if user['role'] != 'admin':
            agent_name = user['name']
            allowed = get_agent_allowed_campaigns(agent_name)
            if lead.get('campaign_type') not in allowed:
                flash('Access denied.', 'error')
                return redirect(url_for('agent_dashboard'))
            assigned_agents = [a.strip() for a in (lead.get('agent_name') or '').split(',') if a.strip()]
            if agent_name not in assigned_agents:
                flash('Access denied.', 'error')
                return redirect(url_for('agent_dashboard'))
            if lead.get('final_status') == 'Follow Up' and lead.get('contacted_by') and lead.get('contacted_by') != agent_name:
                flash('Access denied. This follow-up is owned by another agent.', 'error')
                return redirect(url_for('agent_dashboard'))

        calls_resp = supabase_admin.table('call_attempts').select('*').eq('lead_id', lead_id).order('attempt_number').execute()
        calls = calls_resp.data or []
    except Exception as e:
        flash(f'Error loading lead: {e}', 'error')
        return redirect(url_for('agent_dashboard'))

    return render_template('agent/lead_detail.html',
                           user=user,
                           lead=lead,
                           calls=calls,
                           campaign_label=CAMPAIGN_LABELS.get(lead.get('campaign_type', ''), ''))


@app.route('/agent/leads/<lead_id>/call', methods=['GET', 'POST'])
@login_required
def agent_call_log(lead_id):
    user = get_current_user()

    try:
        lead_resp = supabase_admin.table('leads').select('*').eq('id', lead_id).single().execute()
        lead = lead_resp.data

        if not lead:
            flash('Lead not found.', 'error')
            return redirect(url_for('agent_dashboard'))

        # Enforce agent access rules
        if user['role'] != 'admin':
            agent_name = user['name']
            allowed = get_agent_allowed_campaigns(agent_name)
            if lead.get('campaign_type') not in allowed:
                flash('Access denied.', 'error')
                return redirect(url_for('agent_dashboard'))
            assigned_agents = [a.strip() for a in (lead.get('agent_name') or '').split(',') if a.strip()]
            if agent_name not in assigned_agents:
                flash('Access denied.', 'error')
                return redirect(url_for('agent_dashboard'))
            if lead.get('final_status') == 'Follow Up' and lead.get('contacted_by') and lead.get('contacted_by') != agent_name:
                flash('Access denied. This follow-up is owned by another agent.', 'error')
                return redirect(url_for('agent_dashboard'))

    except Exception as e:
        flash(f'Lead not found: {e}', 'error')
        return redirect(url_for('agent_dashboard'))

    # Check if lead is already converted or enrolled
    if lead.get('final_status') in ['Converted', 'Already Enrolled']:
        flash('This lead is already converted or enrolled and cannot be contacted further.', 'error')
        return redirect(url_for('agent_lead_detail', lead_id=lead_id))

    if request.method == 'POST':
        try:
            connected = request.form.get('connected') == 'true'
            # Parse datetime-local string (local IST) and convert to true UTC for database storage
            called_at_val = request.form.get('called_at')
            if called_at_val:
                try:
                    from datetime import timedelta
                    dt_naive = datetime.fromisoformat(called_at_val)
                    # Treat the naive input as local IST
                    ist = timezone(timedelta(hours=5, minutes=30))
                    dt_local = dt_naive.replace(tzinfo=ist)
                    # Convert to UTC
                    called_at_str = dt_local.astimezone(timezone.utc).isoformat()
                except Exception:
                    called_at_str = datetime.now(timezone.utc).isoformat()
            else:
                called_at_str = datetime.now(timezone.utc).isoformat()

            call_data = {
                'lead_id': lead_id,
                'agent_id': user['id'],
                'agent_name': user['name'],
                'called_at': called_at_str,
                'connected': connected,
            }

            if not connected:
                call_data['not_connected_reason'] = request.form.get('not_connected_reason', 'not_connected')
            else:
                call_status = request.form.get('call_status', '')
                call_data['call_status'] = call_status
                call_data['disposition'] = request.form.get('disposition', '')
                call_data['comments'] = request.form.get('comments', '')

                if call_status == 'follow_up':
                    fu_date_str = request.form.get('follow_up_date', '').strip()
                    fu_time_str = request.form.get('follow_up_time', '').strip()

                    if fu_date_str:
                        try:
                            from datetime import date, time, timedelta
                            ist = timezone(timedelta(hours=5, minutes=30))
                            now_ist = datetime.now(ist)

                            fu_date = date.fromisoformat(fu_date_str)
                            if fu_date < now_ist.date():
                                flash('Follow-up date cannot be in the past.', 'error')
                                return redirect(url_for('agent_call_log', lead_id=lead_id))

                            if fu_date == now_ist.date() and fu_time_str:
                                fu_time = time.fromisoformat(fu_time_str)
                                if fu_time < now_ist.time():
                                    flash('Follow-up time cannot be in the past.', 'error')
                                    return redirect(url_for('agent_call_log', lead_id=lead_id))
                        except Exception as ve:
                            flash(f'Invalid date/time format: {ve}', 'error')
                            return redirect(url_for('agent_call_log', lead_id=lead_id))

                    call_data['follow_up_date'] = fu_date_str or None
                    call_data['follow_up_time'] = fu_time_str or None

                if call_status == 'converted':
                    call_data['amount_paid'] = float(request.form.get('amount_paid') or 0) or None
                    call_data['token_amount'] = float(request.form.get('token_amount') or 0) or None
                    call_data['discount_amount'] = float(request.form.get('discount_amount') or 0) or None
                    call_data['bootcamp_price'] = float(request.form.get('bootcamp_price') or 0) or None
                    call_data['payment_mode'] = request.form.get('payment_mode', '')
                    call_data['payment_reference'] = request.form.get('payment_reference', '')

            supabase_admin.table('call_attempts').insert(call_data).execute()

            # Set contacted_by to the agent who logged the call
            try:
                supabase_admin.table('leads')\
                    .update({'contacted_by': user['name']})\
                    .eq('id', lead_id)\
                    .execute()
            except Exception as e:
                app.logger.error(f"Error updating contacted_by on lead: {e}")

            # Optional Lead Transfer/Reassignment
            transfer_agent = request.form.get('transfer_agent', '').strip()
            if transfer_agent and user['role'] == 'admin':
                supabase_admin.table('leads')\
                    .update({'agent_name': transfer_agent})\
                    .eq('id', lead_id)\
                    .execute()
                flash(f'Call logged and lead successfully transferred to {transfer_agent}!', 'success')
                # Redirect to agent dashboard since this lead is no longer assigned to this agent
                return redirect(url_for('agent_dashboard'))

            flash('Call logged successfully!', 'success')
            return redirect(url_for('agent_lead_detail', lead_id=lead_id))

        except Exception as e:
            flash(f'Error logging call: {str(e)}', 'error')

    # Get next attempt number for display
    try:
        count_resp = supabase_admin.table('call_attempts').select('id, attempt_number').eq('lead_id', lead_id).execute()
        next_attempt = len(count_resp.data or []) + 1
    except Exception:
        next_attempt = 1

    # Fetch all other active agents
    try:
        other_agents_resp = supabase_admin.table('profiles')\
            .select('id,name')\
            .eq('role', 'agent')\
            .eq('is_active', True)\
            .neq('id', user['id'])\
            .execute()
        other_agents = other_agents_resp.data or []
    except Exception:
        other_agents = []

    return render_template('agent/call_log.html',
                           user=user,
                           lead=lead,
                           next_attempt=next_attempt,
                           other_agents=other_agents,
                           campaign_label=CAMPAIGN_LABELS.get(lead.get('campaign_type', ''), ''))


@app.route('/agent/leads/<lead_id>/followup')
@login_required
def agent_followup(lead_id):
    user = get_current_user()
    try:
        lead_resp = supabase_admin.table('leads').select('*').eq('id', lead_id).single().execute()
        lead = lead_resp.data

        if not lead:
            flash('Lead not found.', 'error')
            return redirect(url_for('agent_dashboard'))

        # Enforce agent access rules
        if user['role'] != 'admin':
            agent_name = user['name']
            allowed = get_agent_allowed_campaigns(agent_name)
            if lead.get('campaign_type') not in allowed:
                flash('Access denied.', 'error')
                return redirect(url_for('agent_dashboard'))
            assigned_agents = [a.strip() for a in (lead.get('agent_name') or '').split(',') if a.strip()]
            if agent_name not in assigned_agents:
                flash('Access denied.', 'error')
                return redirect(url_for('agent_dashboard'))
            if lead.get('final_status') == 'Follow Up' and lead.get('contacted_by') and lead.get('contacted_by') != agent_name:
                flash('Access denied. This follow-up is owned by another agent.', 'error')
                return redirect(url_for('agent_dashboard'))

        calls_resp = supabase_admin.table('call_attempts').select('*').eq('lead_id', lead_id).order('attempt_number').execute()
        calls = calls_resp.data or []

        # Find last conversion
        conversion = next((c for c in reversed(calls) if c.get('call_status') == 'converted'), None)
        follow_ups = [c for c in calls if c.get('call_status') == 'follow_up']
    except Exception as e:
        flash(f'Error: {e}', 'error')
        return redirect(url_for('agent_dashboard'))

    return render_template('agent/followup.html',
                           user=user,
                           lead=lead,
                           calls=calls,
                           conversion=conversion,
                           follow_ups=follow_ups,
                           campaign_label=CAMPAIGN_LABELS.get(lead.get('campaign_type', ''), ''))


@app.route('/agent/followups')
@login_required
def agent_followups():
    user = get_current_user()
    agent_name = user['name']
    is_admin = user['role'] == 'admin'

    search = request.args.get('search', '').strip()
    campaign_filter = request.args.get('campaign_type', '')
    priority_filter = request.args.get('priority', '')

    all_camps = [
        ('atpitch_sia',    '📈 Atpitch SIA'),
        ('atpitch_sta',    '📊 Atpitch STA'),
        ('atpitch_others', '🎯 Atpitch Others'),
        ('upsell',         '⬆️ Upsell OB'),
        ('fp_l1',          '💳 FP OB Campaign'),
    ]

    if not is_admin:
        allowed = get_agent_allowed_campaigns(agent_name)
        campaigns_list = [c for c in all_camps if c[0] in allowed]
    else:
        campaigns_list = all_camps

    leads = []
    try:
        if not is_admin and not allowed:
            # Short-circuit if non-admin has no allowed campaigns
            leads = []
        else:
            query = supabase_admin.table('leads').select('*').eq('final_status', 'Follow Up')
            if not is_admin:
                query = query.ilike('agent_name', f'%{agent_name}%')
                query = query.or_(f"contacted_by.is.null,contacted_by.eq.{agent_name}")
                if campaign_filter:
                    if campaign_filter not in allowed:
                        flash('Access denied to this campaign.', 'error')
                        return redirect(url_for('agent_dashboard'))
                    query = query.eq('campaign_type', campaign_filter)
                else:
                    query = query.in_('campaign_type', allowed)
            else:
                if campaign_filter:
                    query = query.eq('campaign_type', campaign_filter)

            if priority_filter:
                query = query.eq('priority', priority_filter)
            if search:
                query = query.or_(f'lead_name.ilike.%{search}%,contact_no.ilike.%{search}%,bootcamp_title.ilike.%{search}%')

            leads_resp = query.order('last_call_date', desc=True).limit(100).execute()
            leads = leads_resp.data or []

            # Enrich with scheduled follow-up date/time from call_attempts
            lead_ids = [l['id'] for l in leads]
            followup_info = {}
            if lead_ids:
                attempts_resp = supabase_admin.table('call_attempts')\
                    .select('lead_id,follow_up_date,follow_up_time')\
                    .in_('lead_id', lead_ids)\
                    .eq('call_status', 'follow_up')\
                    .order('called_at', desc=True)\
                    .execute()

                for att in (attempts_resp.data or []):
                    l_id = att.get('lead_id')
                    if l_id not in followup_info:
                        followup_info[l_id] = {
                            'date': att.get('follow_up_date'),
                            'time': att.get('follow_up_time')
                        }

            for lead in leads:
                info = followup_info.get(lead['id'], {})
                lead['follow_up_date'] = info.get('date') or lead.get('fp_date')
                lead['follow_up_time'] = info.get('time') or lead.get('fp_time')

    except Exception as e:
        leads = []
        flash(f'Error fetching follow-ups: {e}', 'error')

    return render_template('agent/followups.html',
                           user=user,
                           leads=leads,
                           search=search,
                           campaign_filter=campaign_filter,
                           priority_filter=priority_filter,
                           campaigns_list=campaigns_list)


# ============================================================
# API ENDPOINTS (for AJAX)
# ============================================================

@app.route('/api/leads/<lead_id>/update-status', methods=['POST'])
@login_required
def api_update_lead_status(lead_id):
    user = get_current_user()
    data = request.get_json()
    new_status = data.get('status', '')
    if not new_status:
        return jsonify({'error': 'Status required'}), 400
    try:
        # Fetch lead for verification
        lead_resp = supabase_admin.table('leads').select('*').eq('id', lead_id).single().execute()
        lead = lead_resp.data
        if not lead:
            return jsonify({'error': 'Lead not found'}), 404

        if user['role'] != 'admin':
            agent_name = user['name']
            allowed = get_agent_allowed_campaigns(agent_name)
            if lead.get('campaign_type') not in allowed:
                return jsonify({'error': 'Access denied'}), 403
            
            assigned_agents = [a.strip() for a in (lead.get('agent_name') or '').split(',') if a.strip()]
            if agent_name not in assigned_agents:
                return jsonify({'error': 'Access denied'}), 403
            if lead.get('final_status') == 'Follow Up' and lead.get('contacted_by') and lead.get('contacted_by') != agent_name:
                return jsonify({'error': 'Access denied. This follow-up is owned by another agent.'}), 403

        supabase_admin.table('leads').update({
            'final_status': new_status,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }).eq('id', lead_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/search-leads')
@login_required
def api_search_leads():
    q = request.args.get('q', '').strip()
    campaign = request.args.get('campaign', '')
    user = get_current_user()

    if len(q) < 2:
        return jsonify([])
    try:
        query = supabase_admin.table('leads').select(
            'id,lead_name,contact_no,bootcamp_title,final_status,campaign_type'
        ).or_(f'lead_name.ilike.%{q}%,contact_no.ilike.%{q}%').limit(10)

        if user['role'] == 'agent':
            allowed = get_agent_allowed_campaigns(user['name'])
            if not allowed:
                return jsonify([])
            
            if campaign:
                if campaign not in allowed:
                    return jsonify([])
                query = query.eq('campaign_type', campaign)
            else:
                query = query.in_('campaign_type', allowed)
            
            query = query.ilike('agent_name', f'%{user["name"]}%')
        else:
            if campaign:
                query = query.eq('campaign_type', campaign)

        result = query.execute()
        data = result.data or []
        if user.get('role') == 'agent':
            for lead in data:
                if 'contact_no' in lead:
                    lead['contact_no'] = mask_phone(lead['contact_no'], role='agent')
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(413)
def too_large(e):
    flash('File too large. Maximum size is 16MB.', 'error')
    return redirect(url_for('admin_upload'))


# ============================================================
# TEMPLATE FILTERS
# ============================================================

@app.template_filter('format_dt')
def format_dt(value):
    if not value:
        return '—'
    try:
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        else:
            dt = value
        if dt.tzinfo is not None:
            from datetime import timedelta
            ist = timezone(timedelta(hours=5, minutes=30))
            dt = dt.astimezone(ist)
        return dt.strftime('%d %b %Y, %I:%M %p')
    except Exception:
        return str(value)


@app.template_filter('format_time')
def format_time(value):
    if not value:
        return '—'
    try:
        if isinstance(value, str):
            parts = value.split(':')
            if len(parts) >= 2:
                h = int(parts[0])
                m = int(parts[1])
                ampm = 'AM' if h < 12 else 'PM'
                h12 = h % 12
                if h12 == 0:
                    h12 = 12
                return f"{h12:02d}:{m:02d} {ampm}"
        elif hasattr(value, 'hour') and hasattr(value, 'minute'):
            h = value.hour
            m = value.minute
            ampm = 'AM' if h < 12 else 'PM'
            h12 = h % 12
            if h12 == 0:
                h12 = 12
            return f"{h12:02d}:{m:02d} {ampm}"
        return str(value)
    except Exception:
        return str(value)


@app.template_filter('mask_phone')
def mask_phone(value, role='agent'):
    if not value:
        return '—'
    if role == 'admin':
        return value
    val_str = str(value).strip()
    if len(val_str) <= 4:
        return val_str
    first_two = val_str[:2]
    last_two = val_str[-2:]
    middle = '*' * (len(val_str) - 4)
    return f"{first_two}{middle}{last_two}"


@app.template_filter('campaign_label')
def campaign_label_filter(value):
    return CAMPAIGN_LABELS.get(value, value)


@app.template_filter('status_class')
def status_class(status):
    mapping = {
        'Converted': 'badge-success',
        'Follow Up': 'badge-warning',
        'Pending': 'badge-neutral',
        'Already Enrolled': 'badge-info',
        'Not Interested': 'badge-danger',
        'Discarded': 'badge-danger',
        'Need More Detail': 'badge-info',
    }
    return mapping.get(status, 'badge-neutral')


@app.template_filter('priority_class')
def priority_class(p):
    mapping = {
        'P1': 'priority-p1', 'P2': 'priority-p2', 'P3': 'priority-p3',
        'L1': 'priority-p1', 'L2': 'priority-p2'
    }
    return mapping.get(str(p).upper().strip(), 'priority-p3')


@app.template_filter('display_level')
def display_level(priority, campaign_type):
    if not priority:
        return '—'
    p = str(priority).upper().strip()
    if campaign_type == 'fp_l1':
        if p in ['P1', 'L1']:
            return 'L1'
        if p in ['P2', 'P3', 'L2']:
            return 'L2'
        return p
    return p


@app.template_filter('inr')
def inr_format(value):
    if value is None:
        return '—'
    try:
        return f'₹{float(value):,.0f}'
    except Exception:
        return str(value)


@app.template_filter('display_agent_name')
def display_agent_name_filter(value):
    if not value:
        return None
    return str(value).strip()


if __name__ == '__main__':
    app.run(debug=True, port=5000)
