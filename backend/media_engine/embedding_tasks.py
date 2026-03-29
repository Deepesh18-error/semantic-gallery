import threading
from concurrent.futures import ThreadPoolExecutor
from .embedding_engine import run_embedding_pipeline

# --- THE DISPATCHER ---

def start_background_embedding(media_ids):
    """
    The Entry Point called by the View.
    It kicks off a 'Daemon Thread' so the web request can finish instantly.
    """
    if not media_ids:
        return

    print(f"📡 Dispatcher: Received {len(media_ids)} items. Spawning background worker...")

    # 1. We create a dedicated 'Manager Thread'
    # Why? So Django can return '200 OK' to the user right now.
    t = threading.Thread(target=_run_parallel_executor, args=(media_ids,))
    
    # 2. Make it a 'Daemon'
    # This means the thread lives in the background as long as the server is running.
    t.daemon = True
    t.start()

def _run_parallel_executor(media_ids):
    """
    The actual Parallel Engine.
    Creates 8 workers to hit Gemini and Pinecone simultaneously.
    """
    # 3. Use ThreadPoolExecutor (The 'Back Room Staff')
    # We cap at 8 workers to stay under the 10 RPM Gemini limit.
    max_workers = 8
    
    print(f"🧵 ThreadPool: Starting with {max_workers} parallel workers...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 4. 'Map' the Orchestrator to the List of IDs
        # This sends all 8 IDs to the 8 workers at the exact same time.
        executor.map(run_embedding_pipeline, media_ids)

    print("🏁 ThreadPool: All background tasks submitted successfully.")