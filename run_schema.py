#!/usr/bin/env python3
"""
TFU CRM — Database Schema Setup
Runs the SQL schema statements one by one using the Supabase Python client.

Usage:
  source venv/bin/activate
  python run_schema.py
"""
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ['SUPABASE_URL']
SERVICE_KEY  = os.environ['SUPABASE_SERVICE_KEY']

# ── SQL statements split into individual blocks ──────────────
STATEMENTS = [

# 1. Enable UUID extension
"""CREATE EXTENSION IF NOT EXISTS "uuid-ossp";""",

# 2. Profiles table
"""
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'agent')),
    is_active BOOLEAN DEFAULT TRUE,
    password TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
""",

# 3. Leads table
"""
CREATE TABLE IF NOT EXISTS public.leads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    unique_key TEXT UNIQUE NOT NULL,
    campaign_type TEXT NOT NULL CHECK (campaign_type IN (
        'atpitch_sia', 'atpitch_sta', 'atpitch_others',
        'upsell', 'fp_l1', 'fp_l2'
    )),
    lead_type TEXT,
    lead_name TEXT,
    contact_no TEXT NOT NULL,
    bootcamp_title TEXT NOT NULL,
    bootcamp_date TEXT,
    agent_name TEXT,
    contacted_by TEXT,
    priority TEXT,
    email TEXT,
    amount NUMERIC(10,2),
    payment_status TEXT,
    course_level TEXT,
    comment TEXT,
    coupon_code TEXT,
    payment_method_type TEXT,
    fp_date DATE,
    fp_time TIME,
    calling_for_upsell TEXT,
    joining_duration TEXT,
    uploaded_by UUID,
    upload_batch TEXT,
    raw_data JSONB,
    final_status TEXT DEFAULT 'Pending',
    last_call_date TIMESTAMPTZ,
    total_attempts INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
""",

# 4. Leads indexes
"""CREATE INDEX IF NOT EXISTS idx_leads_campaign_type ON public.leads(campaign_type);""",
"""CREATE INDEX IF NOT EXISTS idx_leads_contact_no ON public.leads(contact_no);""",
"""CREATE INDEX IF NOT EXISTS idx_leads_agent_name ON public.leads(agent_name);""",
"""CREATE INDEX IF NOT EXISTS idx_leads_final_status ON public.leads(final_status);""",
"""CREATE INDEX IF NOT EXISTS idx_leads_updated_at ON public.leads(updated_at DESC);""",

# 5. Call attempts table
"""
CREATE TABLE IF NOT EXISTS public.call_attempts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id UUID NOT NULL REFERENCES public.leads(id) ON DELETE CASCADE,
    attempt_number INT NOT NULL,
    agent_id UUID,
    agent_name TEXT,
    called_at TIMESTAMPTZ DEFAULT NOW(),
    connected BOOLEAN NOT NULL DEFAULT FALSE,
    not_connected_reason TEXT CHECK (not_connected_reason IN (
        'not_connected', 'internet_issue', 'call_failure',
        'switched_off', 'busy', 'ringing_no_answer'
    )),
    call_status TEXT CHECK (call_status IN (
        'follow_up', 'converted', 'already_enrolled',
        'need_more_detail', 'not_interested', 'discarded'
    )),
    disposition TEXT,
    comments TEXT,
    follow_up_date DATE,
    follow_up_time TIME,
    follow_up_done BOOLEAN DEFAULT FALSE,
    amount_paid NUMERIC(10,2),
    token_amount NUMERIC(10,2),
    discount_amount NUMERIC(10,2),
    bootcamp_price NUMERIC(10,2),
    payment_mode TEXT,
    payment_reference TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(lead_id, attempt_number)
);
""",

# 6. Call attempts indexes
"""CREATE INDEX IF NOT EXISTS idx_call_attempts_lead_id ON public.call_attempts(lead_id);""",
"""CREATE INDEX IF NOT EXISTS idx_call_attempts_agent_id ON public.call_attempts(agent_id);""",
"""CREATE INDEX IF NOT EXISTS idx_call_attempts_called_at ON public.call_attempts(called_at DESC);""",
"""CREATE INDEX IF NOT EXISTS idx_call_attempts_call_status ON public.call_attempts(call_status);""",

# 7. Upload logs table
"""
CREATE TABLE IF NOT EXISTS public.upload_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    uploaded_by UUID,
    campaign_type TEXT NOT NULL,
    filename TEXT,
    total_rows INT DEFAULT 0,
    inserted_rows INT DEFAULT 0,
    duplicate_rows INT DEFAULT 0,
    error_rows INT DEFAULT 0,
    errors JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
""",

# 8. Auto-increment attempt_number trigger
"""
CREATE OR REPLACE FUNCTION public.set_attempt_number()
RETURNS TRIGGER AS $$
BEGIN
    NEW.attempt_number := COALESCE(
        (SELECT MAX(attempt_number) FROM public.call_attempts WHERE lead_id = NEW.lead_id),
        0
    ) + 1;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""",

"""
DROP TRIGGER IF EXISTS trigger_set_attempt_number ON public.call_attempts;
CREATE TRIGGER trigger_set_attempt_number
BEFORE INSERT ON public.call_attempts
FOR EACH ROW EXECUTE FUNCTION public.set_attempt_number();
""",

# 9. Update lead on call trigger
"""
CREATE OR REPLACE FUNCTION public.update_lead_on_call()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE public.leads SET
        total_attempts = (SELECT COUNT(*) FROM public.call_attempts WHERE lead_id = NEW.lead_id),
        last_call_date = NEW.called_at,
        final_status = CASE
            WHEN NEW.call_status = 'converted'        THEN 'Converted'
            WHEN NEW.call_status = 'already_enrolled' THEN 'Already Enrolled'
            WHEN NEW.call_status = 'not_interested'   THEN 'Not Interested'
            WHEN NEW.call_status = 'discarded'        THEN 'Discarded'
            WHEN NEW.call_status = 'follow_up'        THEN 'Follow Up'
            WHEN NEW.call_status = 'call_back_later'   THEN 'Call Back Later'
            WHEN NEW.call_status = 'need_more_detail'  THEN 'Follow Up'
            ELSE final_status
        END,
        updated_at = NOW()
    WHERE id = NEW.lead_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""",

"""
DROP TRIGGER IF EXISTS trigger_update_lead_on_call ON public.call_attempts;
CREATE TRIGGER trigger_update_lead_on_call
AFTER INSERT OR UPDATE ON public.call_attempts
FOR EACH ROW EXECUTE FUNCTION public.update_lead_on_call();
""",

# 10. updated_at trigger function
"""
CREATE OR REPLACE FUNCTION public.handle_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""",

"""
DROP TRIGGER IF EXISTS trigger_profiles_updated_at ON public.profiles;
CREATE TRIGGER trigger_profiles_updated_at
BEFORE UPDATE ON public.profiles
FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();
""",

"""
DROP TRIGGER IF EXISTS trigger_leads_updated_at ON public.leads;
CREATE TRIGGER trigger_leads_updated_at
BEFORE UPDATE ON public.leads
FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();
""",

"""
DROP TRIGGER IF EXISTS trigger_call_attempts_updated_at ON public.call_attempts;
CREATE TRIGGER trigger_call_attempts_updated_at
BEFORE UPDATE ON public.call_attempts
FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();
""",

# 11. Enable RLS
"""ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;""",
"""ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;""",
"""ALTER TABLE public.call_attempts ENABLE ROW LEVEL SECURITY;""",
"""ALTER TABLE public.upload_logs ENABLE ROW LEVEL SECURITY;""",

# 12. RLS Policies — profiles (allow service role full access, used by Flask backend)
"""
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE tablename = 'profiles' AND policyname = 'profiles_service_all'
  ) THEN
    CREATE POLICY profiles_service_all ON public.profiles FOR ALL USING (true);
  END IF;
END $$;
""",

"""
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE tablename = 'leads' AND policyname = 'leads_service_all'
  ) THEN
    CREATE POLICY leads_service_all ON public.leads FOR ALL USING (true);
  END IF;
END $$;
""",

"""
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE tablename = 'call_attempts' AND policyname = 'call_attempts_service_all'
  ) THEN
    CREATE POLICY call_attempts_service_all ON public.call_attempts FOR ALL USING (true);
  END IF;
END $$;
""",

"""
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE tablename = 'upload_logs' AND policyname = 'upload_logs_service_all'
  ) THEN
    CREATE POLICY upload_logs_service_all ON public.upload_logs FOR ALL USING (true);
  END IF;
END $$;
""",
]


def run_sql_via_api(sql: str, desc: str) -> bool:
    """Execute SQL via Supabase's pg REST endpoint."""
    headers = {
        'apikey': SERVICE_KEY,
        'Authorization': f'Bearer {SERVICE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal',
    }
    # Use the Supabase /sql endpoint (available since 2024)
    r = httpx.post(
        f'{SUPABASE_URL}/rest/v1/rpc/exec',
        headers=headers,
        json={'statement': sql},
        timeout=30
    )
    if r.status_code in [200, 201, 204]:
        return True
    # Try alternative: direct pg connection via supabase-py
    return False


def run_via_supabase_py(sql: str) -> tuple[bool, str]:
    """Use supabase-py to run raw SQL via the postgres wrapper."""
    try:
        from supabase import create_client
        client = create_client(SUPABASE_URL, SERVICE_KEY)
        # supabase-py 2.x uses postgrest which supports rpc
        # We need to use the database direct query
        result = client.postgrest.schema('public')
        return True, "ok"
    except Exception as e:
        return False, str(e)


def main():
    print("=" * 60)
    print("  TFU CRM — Database Schema Setup")
    print("=" * 60)
    print(f"\n📡 Supabase URL: {SUPABASE_URL}")
    print(f"🔑 Using service key: {SERVICE_KEY[:30]}...")
    print()

    # Try using httpx directly to the Supabase /sql endpoint
    headers = {
        'apikey': SERVICE_KEY,
        'Authorization': f'Bearer {SERVICE_KEY}',
        'Content-Type': 'application/json',
    }

    total = len(STATEMENTS)
    ok = 0
    errors = []

    for i, sql in enumerate(STATEMENTS, 1):
        sql = sql.strip()
        if not sql:
            continue

        # Get a short description
        first_line = sql.split('\n')[0].strip()[:60]

        # Try via Supabase SQL API (v2)
        try:
            r = httpx.post(
                f'{SUPABASE_URL}/rest/v1/rpc/query',
                headers={**headers, 'Prefer': 'return=minimal'},
                json={'query': sql},
                timeout=30
            )

            if r.status_code in [200, 201, 204]:
                print(f"  ✅ [{i}/{total}] {first_line}")
                ok += 1
            else:
                # If the function doesn't exist, schema needs to be run manually
                if 'PGRST202' in r.text or '404' in str(r.status_code):
                    print(f"  ⚠️  [{i}/{total}] Direct SQL API not available (use Supabase SQL Editor)")
                    errors.append((i, first_line, "Use Supabase SQL Editor"))
                    break
                else:
                    print(f"  ❌ [{i}/{total}] {first_line}")
                    print(f"         Error: {r.text[:150]}")
                    errors.append((i, first_line, r.text[:150]))
        except Exception as e:
            print(f"  ❌ [{i}/{total}] Exception: {e}")
            errors.append((i, first_line, str(e)))

    print()
    if errors and 'SQL Editor' in str(errors[0]):
        print("━" * 60)
        print("📋 MANUAL SETUP REQUIRED")
        print("━" * 60)
        print()
        print("Supabase doesn't allow direct SQL execution via REST API.")
        print("Please run the schema manually:")
        print()
        print("  1. Go to: https://supabase.com/dashboard/project/pcmssvfghrpxjossanej")
        print("  2. Click 'SQL Editor' in the left sidebar")
        print("  3. Click '+ New query'")
        print("  4. Open: /Users/aman/Downloads/TFU LEADSQR/supabase/001_init.sql")
        print("  5. Paste the contents and click 'Run'")
        print()
        print("✅ The SQL file is ready at:")
        print("   /Users/aman/Downloads/TFU LEADSQR/supabase/001_init.sql")
    elif ok == total:
        print(f"✅ Schema created successfully! ({ok}/{total} statements)")
    else:
        print(f"⚠️  {ok}/{total} statements ran. {len(errors)} errors.")

if __name__ == '__main__':
    main()
