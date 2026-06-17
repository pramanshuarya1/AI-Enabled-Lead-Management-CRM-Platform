-- Migration: Add 'cut_the_call' to not_connected_reason CHECK constraint
-- Please execute this SQL block in your Supabase SQL Editor (https://supabase.com/dashboard/project/pcmssvfghrpxjossanej/sql/new)

ALTER TABLE public.call_attempts
DROP CONSTRAINT IF EXISTS call_attempts_not_connected_reason_check;

ALTER TABLE public.call_attempts
ADD CONSTRAINT call_attempts_not_connected_reason_check
CHECK (not_connected_reason IN (
    'not_connected', 'internet_issue', 'call_failure', 'switched_off', 'busy', 'ringing_no_answer', 'cut_the_call'
));
