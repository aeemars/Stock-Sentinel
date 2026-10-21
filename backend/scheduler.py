from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
from app.config import settings
from app.services.workflow import run_root_workflow


_scheduler = None

def _job():
    # This runs at market close and triggers the AI analysis workflow
    print(f"[Scheduler] Running AI analysis workflow at {datetime.now()}")
    try:
        run_root_workflow()
    except Exception as e:
        print('Error running workflow:', e)

def start_scheduler(app=None):
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    scheduler = BackgroundScheduler()
    # Run every weekday at configured market close time
    trigger = CronTrigger(
        day_of_week='mon-fri',
        hour=settings.MARKET_CLOSE_HOUR,
        minute=settings.MARKET_CLOSE_MINUTE
    )
    scheduler.add_job(_job, trigger, id='daily_ai_analysis')
    scheduler.start()
    _scheduler = scheduler
    print(f"Scheduler started: daily AI analysis job at {settings.MARKET_CLOSE_HOUR}:{settings.MARKET_CLOSE_MINUTE}")
    return scheduler

def stop_scheduler():
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown()
        _scheduler = None
        print("Scheduler stopped.")
