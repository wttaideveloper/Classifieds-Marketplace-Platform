"""Start-up of the meetings service: the retry job for failed meeting links (was started by every monolith process)."""
from mskit.jobs import job

HOOKS = [job("meetings_service.services.meeting_service:start_scheduler")]
