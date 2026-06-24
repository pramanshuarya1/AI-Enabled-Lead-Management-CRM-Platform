-- ============================================================
-- Migration 004: Add 'not_attended_class' call status support
-- ============================================================

-- 1. Update the CHECK constraint for call_status to include 'not_attended_class'
-- Please execute this SQL block in your Supabase SQL Editor (https://supabase.com/dashboard/project/pcmssvfghrpxjossanej/sql/new)

ALTER TABLE public.call_attempts
DROP CONSTRAINT IF EXISTS call_attempts_call_status_check;

ALTER TABLE public.call_attempts
ADD CONSTRAINT call_attempts_call_status_check
CHECK (call_status IN (
    'follow_up', 'converted', 'already_enrolled', 'need_more_detail', 
    'not_interested', 'discarded', 'call_back_later', 'cut_the_call', 'not_attended_class'
));

-- 2. Define the complete cumulative update_lead_on_call trigger function
CREATE OR REPLACE FUNCTION public.update_lead_on_call()
RETURNS TRIGGER AS $$
DECLARE
    old_status TEXT;
BEGIN
    SELECT final_status INTO old_status FROM public.leads WHERE id = NEW.lead_id;

    UPDATE public.leads SET
        total_attempts = (SELECT COUNT(*) FROM public.call_attempts WHERE lead_id = NEW.lead_id),
        last_call_date = NEW.called_at,
        final_status = CASE 
            -- If unconnected or cut call AND lead is already in a follow-up status, retain it
            WHEN (NEW.connected = FALSE OR NEW.call_status = 'cut_the_call') AND old_status IN ('Follow Up', 'Call Back Later', 'Need More Detail') THEN
                old_status

            WHEN NEW.connected = FALSE THEN
                CASE 
                    WHEN NEW.not_connected_reason = 'ringing_no_answer' THEN 'DNP'
                    WHEN NEW.not_connected_reason = 'switched_off' THEN 'Switched Off'
                    WHEN NEW.not_connected_reason = 'busy' THEN 'Line Busy'
                    WHEN NEW.not_connected_reason = 'internet_issue' THEN 'Internet Issue'
                    WHEN NEW.not_connected_reason = 'call_failure' THEN 'Call Failure'
                    ELSE 'Not Connected'
                END
            WHEN NEW.call_status = 'converted'          THEN 'Converted'
            WHEN NEW.call_status = 'already_enrolled'   THEN 'Already Enrolled'
            WHEN NEW.call_status = 'not_interested'     THEN 'Not Interested'
            WHEN NEW.call_status = 'discarded'          THEN 'Discarded'
            WHEN NEW.call_status = 'follow_up'          THEN 'Follow Up'
            WHEN NEW.call_status = 'call_back_later'    THEN 'Call Back Later'
            WHEN NEW.call_status = 'need_more_detail'   THEN 'Need More Detail'
            WHEN NEW.call_status = 'cut_the_call'       THEN 'Cut the Call'
            WHEN NEW.call_status = 'not_attended_class' THEN 'Not Attended Class'
            ELSE final_status
        END,
        updated_at = NOW()
    WHERE id = NEW.lead_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3. Re-create the trigger to bind it fresh
DROP TRIGGER IF EXISTS trigger_update_lead_on_call ON public.call_attempts;

CREATE TRIGGER trigger_update_lead_on_call
AFTER INSERT OR UPDATE ON public.call_attempts
FOR EACH ROW EXECUTE FUNCTION public.update_lead_on_call();
