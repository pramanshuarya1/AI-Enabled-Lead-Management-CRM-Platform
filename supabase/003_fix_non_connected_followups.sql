-- Migration: Fix Reverting of Follow Up Leads to Pending on Unconnected Calls & Fix Cluttered Follow-ups with Conditional DNP Logic
-- Please execute this SQL block in your Supabase SQL Editor (https://supabase.com/dashboard/project/pcmssvfghrpxjossanej/sql/new)

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
            WHEN NEW.call_status = 'converted' THEN 'Converted'
            WHEN NEW.call_status = 'already_enrolled' THEN 'Already Enrolled'
            WHEN NEW.call_status = 'not_interested' THEN 'Not Interested'
            WHEN NEW.call_status = 'discarded' THEN 'Discarded'
            WHEN NEW.call_status = 'follow_up' THEN 'Follow Up'
            WHEN NEW.call_status = 'call_back_later' THEN 'Call Back Later'
            WHEN NEW.call_status = 'need_more_detail' THEN 'Need More Detail'
            WHEN NEW.call_status = 'cut_the_call' THEN 'Cut the Call'
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

-- Update CHECK constraint for call_status to allow 'call_back_later' and 'cut_the_call'
ALTER TABLE public.call_attempts
DROP CONSTRAINT IF EXISTS call_attempts_call_status_check;

ALTER TABLE public.call_attempts
ADD CONSTRAINT call_attempts_call_status_check
CHECK (call_status IN (
    'follow_up', 'converted', 'already_enrolled', 'need_more_detail', 'not_interested', 'discarded', 'call_back_later', 'cut_the_call'
));

-- Clean up existing leads whose final_status does not align with their latest call attempt status and prior followups
WITH ranked_attempts AS (
    SELECT 
        lead_id,
        connected,
        not_connected_reason,
        call_status,
        called_at,
        ROW_NUMBER() OVER (PARTITION BY lead_id ORDER BY called_at DESC) as rn
    FROM public.call_attempts
),
latest_attempts AS (
    SELECT 
        lead_id,
        connected,
        not_connected_reason,
        call_status
    FROM ranked_attempts 
    WHERE rn = 1
),
prior_followups AS (
    SELECT DISTINCT ON (lead_id)
        lead_id,
        call_status
    FROM public.call_attempts
    WHERE call_status IN ('follow_up', 'call_back_later', 'need_more_detail')
      AND called_at < (SELECT MAX(called_at) FROM public.call_attempts ca2 WHERE ca2.lead_id = public.call_attempts.lead_id)
    ORDER BY lead_id, called_at DESC
)
UPDATE public.leads l
SET final_status = CASE 
        -- If latest attempt was unconnected or cut, AND there was a prior follow-up attempt
        WHEN (la.connected = FALSE OR la.call_status = 'cut_the_call') AND pf.call_status IS NOT NULL THEN
            CASE 
                WHEN pf.call_status = 'follow_up' THEN 'Follow Up'
                WHEN pf.call_status = 'call_back_later' THEN 'Call Back Later'
                WHEN pf.call_status = 'need_more_detail' THEN 'Need More Detail'
                ELSE 'Follow Up'
            END

        -- If latest attempt was unconnected or cut, and NO prior follow-up attempt existed
        WHEN la.connected = FALSE THEN
            CASE 
                WHEN la.not_connected_reason = 'ringing_no_answer' THEN 'DNP'
                WHEN la.not_connected_reason = 'switched_off' THEN 'Switched Off'
                WHEN la.not_connected_reason = 'busy' THEN 'Line Busy'
                WHEN la.not_connected_reason = 'internet_issue' THEN 'Internet Issue'
                WHEN la.not_connected_reason = 'call_failure' THEN 'Call Failure'
                ELSE 'Not Connected'
            END
        WHEN la.call_status = 'cut_the_call' THEN 'Cut the Call'

        -- Connected outcomes
        WHEN la.call_status = 'converted' THEN 'Converted'
        WHEN la.call_status = 'already_enrolled' THEN 'Already Enrolled'
        WHEN la.call_status = 'not_interested' THEN 'Not Interested'
        WHEN la.call_status = 'discarded' THEN 'Discarded'
        WHEN la.call_status = 'follow_up' THEN 'Follow Up'
        WHEN la.call_status = 'call_back_later' THEN 'Call Back Later'
        WHEN la.call_status = 'need_more_detail' THEN 'Need More Detail'
        ELSE l.final_status
    END,
    updated_at = NOW()
FROM latest_attempts la
LEFT JOIN prior_followups pf ON la.lead_id = pf.lead_id
WHERE l.id = la.lead_id;

