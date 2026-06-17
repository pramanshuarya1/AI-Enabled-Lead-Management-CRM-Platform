-- Migration: Fix Reverting of Follow Up Leads to Pending on Unconnected Calls
-- Please execute this SQL block in your Supabase SQL Editor (https://supabase.com/dashboard/project/pcmssvfghrpxjossanej/sql/new)

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
            ELSE final_status
        END,
        updated_at = NOW()
    WHERE id = NEW.lead_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Recreate trigger to ensure it binding is fresh
DROP TRIGGER IF EXISTS trigger_update_lead_on_call ON public.call_attempts;

CREATE TRIGGER trigger_update_lead_on_call
AFTER INSERT OR UPDATE ON public.call_attempts
FOR EACH ROW EXECUTE FUNCTION public.update_lead_on_call();
