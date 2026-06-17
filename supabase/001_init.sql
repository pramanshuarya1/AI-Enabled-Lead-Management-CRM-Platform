-- ============================================================
-- TFU LeadSquared CRM — Supabase SQL Schema
-- Run this in your Supabase SQL Editor
-- ============================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. USER PROFILES (extends Supabase Auth)
-- ============================================================
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'agent')),
    is_active BOOLEAN DEFAULT TRUE,
    password TEXT,
    campaigns TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS for profiles
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "profiles_self_read" ON public.profiles FOR SELECT USING (auth.uid() = id);
CREATE POLICY "profiles_admin_all" ON public.profiles FOR ALL USING (
    EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin')
);

-- ============================================================
-- 2. LEADS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS public.leads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    unique_key TEXT UNIQUE NOT NULL,   -- phone + '_' + bootcamp_title (dedup)
    
    -- Campaign classification
    campaign_type TEXT NOT NULL CHECK (campaign_type IN (
        'atpitch_sia', 'atpitch_sta', 'atpitch_others', 
        'upsell', 'fp_l1', 'fp_l2'
    )),
    lead_type TEXT,  -- SIA, STA, FP_L1, FP_L1_High, FP_L2, FP_L1_Low
    
    -- Core lead info
    lead_name TEXT,
    contact_no TEXT NOT NULL,
    bootcamp_title TEXT NOT NULL,
    bootcamp_date TEXT,
    agent_name TEXT,
    contacted_by TEXT,
    priority TEXT,  -- P1, P2, P3
    
    -- FP-specific fields
    email TEXT,
    amount NUMERIC(10,2),
    payment_status TEXT,
    course_level TEXT,  -- L1, L2
    comment TEXT,
    coupon_code TEXT,
    payment_method_type TEXT,
    fp_date DATE,
    fp_time TIME,
    
    -- Atpitch/Upsell specific
    calling_for_upsell TEXT,
    joining_duration TEXT,
    
    -- Upload metadata
    uploaded_by UUID REFERENCES public.profiles(id),
    upload_batch TEXT,  -- timestamp of upload batch for grouping
    raw_data JSONB,     -- full original row stored as JSON
    
    -- Status tracking
    final_status TEXT DEFAULT 'Pending',
    last_call_date TIMESTAMPTZ,
    total_attempts INT DEFAULT 0,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX idx_leads_campaign_type ON public.leads(campaign_type);
CREATE INDEX idx_leads_contact_no ON public.leads(contact_no);
CREATE INDEX idx_leads_agent_name ON public.leads(agent_name);
CREATE INDEX idx_leads_final_status ON public.leads(final_status);
CREATE INDEX idx_leads_priority ON public.leads(priority);
CREATE INDEX idx_leads_updated_at ON public.leads(updated_at DESC);

-- RLS
ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;
CREATE POLICY "leads_admin_all" ON public.leads FOR ALL USING (
    EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin')
);
CREATE POLICY "leads_agent_own" ON public.leads FOR SELECT USING (
    EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() 
            AND p.role = 'agent' AND p.name = leads.agent_name)
);

-- ============================================================
-- 3. CALL ATTEMPTS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS public.call_attempts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id UUID NOT NULL REFERENCES public.leads(id) ON DELETE CASCADE,
    attempt_number INT NOT NULL,  -- auto-incremented per lead
    agent_id UUID REFERENCES public.profiles(id),
    agent_name TEXT,
    
    -- Call timing
    called_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Connection status
    connected BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- If NOT connected:
    not_connected_reason TEXT CHECK (not_connected_reason IN (
        'not_connected', 'internet_issue', 'call_failure', 'switched_off', 'busy', 'ringing_no_answer', 'cut_the_call'
    )),
    
    -- If connected:
    call_status TEXT CHECK (call_status IN (
        'follow_up', 'converted', 'already_enrolled', 'need_more_detail', 'not_interested', 'discarded', 'call_back_later'
    )),
    disposition TEXT,
    comments TEXT,
    
    -- Follow-up scheduling
    follow_up_date DATE,
    follow_up_time TIME,
    follow_up_done BOOLEAN DEFAULT FALSE,
    
    -- Conversion details (filled when call_status = 'converted')
    amount_paid NUMERIC(10,2),
    token_amount NUMERIC(10,2),
    discount_amount NUMERIC(10,2),
    bootcamp_price NUMERIC(10,2),
    payment_mode TEXT,  -- 'cash', 'upi', 'card', 'bank_transfer'
    payment_reference TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Each attempt number must be unique per lead
    UNIQUE(lead_id, attempt_number)
);

-- Indexes
CREATE INDEX idx_call_attempts_lead_id ON public.call_attempts(lead_id);
CREATE INDEX idx_call_attempts_agent_id ON public.call_attempts(agent_id);
CREATE INDEX idx_call_attempts_called_at ON public.call_attempts(called_at DESC);
CREATE INDEX idx_call_attempts_call_status ON public.call_attempts(call_status);
CREATE INDEX idx_call_attempts_follow_up_date ON public.call_attempts(follow_up_date);

-- RLS
ALTER TABLE public.call_attempts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "call_attempts_admin_all" ON public.call_attempts FOR ALL USING (
    EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin')
);
CREATE POLICY "call_attempts_agent_own" ON public.call_attempts FOR ALL USING (
    agent_id = auth.uid()
);

-- ============================================================
-- 4. AUTO-INCREMENT attempt_number TRIGGER
-- ============================================================
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

CREATE TRIGGER trigger_set_attempt_number
BEFORE INSERT ON public.call_attempts
FOR EACH ROW EXECUTE FUNCTION public.set_attempt_number();

-- ============================================================
-- 5. UPDATE lead.total_attempts AND last_call_date TRIGGER
-- ============================================================
CREATE OR REPLACE FUNCTION public.update_lead_on_call()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE public.leads SET
        total_attempts = (SELECT COUNT(*) FROM public.call_attempts WHERE lead_id = NEW.lead_id),
        last_call_date = NEW.called_at,
        final_status = CASE 
            WHEN NEW.call_status = 'converted' THEN 'Converted'
            WHEN NEW.call_status = 'already_enrolled' THEN 'Already Enrolled'
            WHEN NEW.call_status = 'not_interested' THEN 'Not Interested'
            WHEN NEW.call_status = 'discarded' THEN 'Discarded'
            WHEN NEW.call_status = 'follow_up' THEN 'Follow Up'
            WHEN NEW.call_status = 'call_back_later' THEN 'Call Back Later'
            WHEN NEW.call_status = 'need_more_detail' THEN 'Follow Up'
            ELSE final_status
        END,
        updated_at = NOW()
    WHERE id = NEW.lead_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_lead_on_call
AFTER INSERT OR UPDATE ON public.call_attempts
FOR EACH ROW EXECUTE FUNCTION public.update_lead_on_call();

-- ============================================================
-- 6. UPDATED_AT TRIGGER (for all tables)
-- ============================================================
CREATE OR REPLACE FUNCTION public.handle_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_profiles_updated_at
BEFORE UPDATE ON public.profiles
FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

CREATE TRIGGER trigger_leads_updated_at
BEFORE UPDATE ON public.leads
FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

CREATE TRIGGER trigger_call_attempts_updated_at
BEFORE UPDATE ON public.call_attempts
FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

-- ============================================================
-- 7. UPLOAD BATCHES LOG
-- ============================================================
CREATE TABLE IF NOT EXISTS public.upload_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    uploaded_by UUID REFERENCES public.profiles(id),
    campaign_type TEXT NOT NULL,
    filename TEXT,
    total_rows INT DEFAULT 0,
    inserted_rows INT DEFAULT 0,
    duplicate_rows INT DEFAULT 0,
    error_rows INT DEFAULT 0,
    errors JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE public.upload_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "upload_logs_admin_all" ON public.upload_logs FOR ALL USING (
    EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin')
);

-- ============================================================
-- SAMPLE ADMIN USER (run after setting up auth user)
-- Replace the UUID with the actual auth.users UUID after signup
-- INSERT INTO public.profiles (id, name, email, role)
-- VALUES ('your-auth-user-uuid', 'Admin User', 'admin@tfu.com', 'admin');
-- ============================================================
