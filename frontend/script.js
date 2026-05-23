const API_BASE_URL = import.meta.env.VITE_SERVER_URL || 'http://127.0.0.1:8000';

let lastCallId = '';

// ─── Tab Switching ──────────────────────────────────────────────────────

window.switchTab = function (tabId) {
    document.querySelectorAll('.tab-btn').forEach((btn) => {
        btn.classList.remove('active');
        if (btn.getAttribute('onclick').includes(tabId)) {
            btn.classList.add('active');
        }
    });

    document.querySelectorAll('.tab-content').forEach((content) => {
        content.classList.remove('active');
    });
    document.getElementById(tabId).classList.add('active');
};

// ─── Advanced Options Toggle ────────────────────────────────────────────

window.toggleAdvanced = function () {
    const content = document.getElementById('advanced-options');
    const icon = document.getElementById('advanced-icon');
    content.classList.toggle('open');
    icon.classList.toggle('open');
};

// ─── Toast ──────────────────────────────────────────────────────────────

function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast show ${type}`;
    setTimeout(() => {
        toast.className = 'toast';
    }, 3500);
}

// ─── Copy Call ID ───────────────────────────────────────────────────────

window.copyCallId = function () {
    navigator.clipboard.writeText(lastCallId).then(() => {
        showToast('Call ID copied!', 'success');
    });
};

// ─── Make Call ───────────────────────────────────────────────────────────

document.getElementById('make-call-btn').addEventListener('click', async (e) => {
    const phone = document.getElementById('phone').value.trim();
    const systemPrompt = document.getElementById('system_prompt').value.trim();
    const context = document.getElementById('context').value.trim();
    const firstMessage = document.getElementById('first_message').value.trim();
    const language = document.getElementById('language').value;
    const speaker = document.getElementById('speaker').value;
    const webhookUrl = document.getElementById('webhook_url').value.trim();

    if (!phone) {
        showToast('Phone number is required', 'error');
        return;
    }
    if (!systemPrompt) {
        showToast('System prompt is required', 'error');
        return;
    }

    const btn = e.currentTarget;
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span> Calling...';

    try {
        const body = {
            phone_number: phone,
            system_prompt: systemPrompt,
            voice: { language, speaker },
        };
        if (context) body.context = context;
        if (firstMessage) body.first_message = firstMessage;
        if (webhookUrl) body.webhook_url = webhookUrl;

        const response = await fetch(`${API_BASE_URL}/make-call`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        const data = await response.json();

        if (data.status === 'initiated') {
            lastCallId = data.call_id;

            // Show the call ID
            const display = document.getElementById('call-id-display');
            display.classList.remove('hidden');
            document.getElementById('call-id-text').textContent = data.call_id;

            // Also prefill the lookup field
            document.getElementById('lookup-call-id').value = data.call_id;

            showToast('Call initiated successfully!', 'success');
        } else {
            showToast(data.message || 'Failed to initiate call', 'error');
        }
    } catch (error) {
        showToast('Failed to connect to server', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span class="btn-icon">📞</span> Make Call';
    }
});

// ─── Look Up Call Result ────────────────────────────────────────────────

document.getElementById('lookup-btn').addEventListener('click', async (e) => {
    const callId = document.getElementById('lookup-call-id').value.trim();
    if (!callId) {
        showToast('Enter a Call ID', 'error');
        return;
    }

    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = 'Loading...';

    try {
        const response = await fetch(`${API_BASE_URL}/call/${encodeURIComponent(callId)}`);
        const data = await response.json();

        const container = document.getElementById('result-container');

        if (data.status === 'not_found') {
            container.classList.add('hidden');
            showToast('Call not found', 'error');
            return;
        }

        // Show results
        container.classList.remove('hidden');

        // Status badge
        const statusBadge = document.getElementById('result-status');
        statusBadge.textContent = data.status;
        statusBadge.className = `status-badge ${data.status}`;

        // Duration
        const durationEl = document.getElementById('result-duration');
        if (data.duration_seconds) {
            const mins = Math.floor(data.duration_seconds / 60);
            const secs = data.duration_seconds % 60;
            durationEl.textContent = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
        } else {
            durationEl.textContent = '';
        }

        // Sentiment badge
        const sentimentBadge = document.getElementById('result-sentiment-badge');
        if (data.user_sentiment) {
            const sentimentLabels = {
                positive: '😊 Positive',
                negative: '😞 Negative',
                neutral: '😐 Neutral',
                mixed: '🤔 Mixed',
            };
            sentimentBadge.textContent = sentimentLabels[data.user_sentiment] || data.user_sentiment;
            sentimentBadge.className = `sentiment-badge ${data.user_sentiment}`;
        } else {
            sentimentBadge.textContent = '—';
            sentimentBadge.className = 'sentiment-badge';
        }

        // Summary
        document.getElementById('result-summary').textContent = data.summary || '—';

        // Sentiment detail
        document.getElementById('result-sentiment-detail').textContent = data.sentiment_detail || '—';

        // Transcript
        const transcriptEl = document.getElementById('result-transcript');
        if (data.transcript && data.transcript.length > 0) {
            transcriptEl.innerHTML = data.transcript
                .map(
                    (msg) => `
                    <div class="transcript-msg ${msg.role}">
                        <div class="transcript-role">${msg.role === 'assistant' ? 'Bot' : 'User'}</div>
                        <div>${escapeHtml(msg.content)}</div>
                    </div>
                `
                )
                .join('');
        } else {
            transcriptEl.innerHTML = '<p class="result-text muted">No transcript available yet.</p>';
        }

        // Scroll into view
        container.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) {
        showToast('Failed to connect to server', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Look Up';
    }
});

// ─── Utilities ──────────────────────────────────────────────────────────

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
