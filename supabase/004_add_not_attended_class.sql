-- ============================================================
-- Migration 004: Add 'not_attended_class' call status support
-- ============================================================

-- Update the update_lead_on_call trigger to map
-- not_attended_class → 'Not Attended Class' final_status
CREATE OR REPLACE FUNCTION public.update_lead_on_call()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE public.leads SET
        total_attempts = (SELECT COUNT(*) FROM public.call_attempts WHERE lead_id = NEW.lead_id),
        last_call_date = NEW.called_at,
        final_status = CASE
            WHEN NEW.call_status = 'converted'            THEN 'Converted'
            WHEN NEW.call_status = 'already_enrolled'     THEN 'Already Enrolled'
            WHEN NEW.call_status = 'not_interested'       THEN 'Not Interested'
            WHEN NEW.call_status = 'discarded'            THEN 'Discarded'
            WHEN NEW.call_status = 'follow_up'            THEN 'Follow Up'
            WHEN NEW.call_status = 'call_back_later'      THEN 'Call Back Later'
            WHEN NEW.call_status = 'need_more_detail'     THEN 'Follow Up'
            WHEN NEW.call_status = 'not_attended_class'   THEN 'Not Attended Class'
            ELSE final_status
        END,
        updated_at = NOW()
    WHERE id = NEW.lead_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
