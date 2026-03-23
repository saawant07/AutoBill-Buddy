import os

filepath = 'static/billing.html'
with open(filepath, 'r') as f:
    lines = f.readlines()

start_idx = -1
end_idx = -1

for i, line in enumerate(lines):
    if 'let recognition = null;' in line and start_idx == -1:
        start_idx = i
    if '// Customer voice (uses LOCAL recognition' in line and start_idx != -1:
        end_idx = i
        break

if start_idx != -1 and end_idx != -1:
    new_script = """        let mediaRecorder = null;
        let audioChunks = [];
        let isListening = false;
        let voiceMode = null;
        let audioStream = null;

        // Start generic audio recording
        async function startRecording(mode) {
            if (isListening) {
                stopVoice();
                if (voiceMode === mode) return; // Toggle logic
            }

            try {
                audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                console.log(`[Voice] Mic permission granted for ${mode}`);
            } catch(e) {
                console.log(`[Voice] Mic permission denied:`, e.message);
                app.showToast('Please allow microphone in browser settings', 'error');
                return;
            }

            voiceMode = mode;
            audioChunks = [];
            isListening = true;
            
            mediaRecorder = new MediaRecorder(audioStream);
            
            mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    audioChunks.push(event.data);
                }
            };

            mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                audioChunks = [];
                if (audioStream) {
                    audioStream.getTracks().forEach(track => track.stop());
                }
                
                await uploadAudioToGemini(audioBlob, voiceMode);
                isListening = false;
                voiceMode = null;
            };

            mediaRecorder.start();

            // UI Updates
            if (mode === 'persistent') {
                const pBtn = document.getElementById('voice-persistent-btn');
                const pLabel = document.getElementById('voice-persistent-label');
                if (pBtn) {
                    pBtn.classList.add('listening');
                    pBtn.innerHTML = '<i class="fas fa-stop" style="font-size:20px;"></i>';
                }
                if (pLabel) pLabel.textContent = 'LISTENING... TAP TO STOP';
            } else if (mode === 'quick') {
                const qBtn = document.getElementById('quick-add-btn');
                if (qBtn) qBtn.classList.add('listening');
            }
        }

        window.togglePersistentVoice = () => startRecording('persistent');
        window.startQuickAdd = () => startRecording('quick');

        window.stopVoice = function() {
            if (mediaRecorder && mediaRecorder.state !== 'inactive') {
                mediaRecorder.stop();
            } else {
                isListening = false;
                voiceMode = null;
                resetVoiceUI();
            }
        };

        function resetVoiceUI() {
            const pBtn = document.getElementById('voice-persistent-btn');
            const pLabel = document.getElementById('voice-persistent-label');
            if (pBtn) {
                pBtn.classList.remove('listening');
                pBtn.innerHTML = '<i class="fas fa-microphone" style="font-size:20px;"></i>';
            }
            if (pLabel && !pLabel.textContent.includes('PARSING')) {
                pLabel.textContent = 'TAP TO ADD BY VOICE';
            }
            const qBtn = document.getElementById('quick-add-btn');
            if (qBtn) qBtn.classList.remove('listening');
        }

        async function uploadAudioToGemini(blob, mode) {
            if (blob.size === 0) {
                app.showToast('No audio recorded', 'warning');
                resetVoiceUI();
                return;
            }

            // Show Parsing UI
            if (mode === 'persistent') {
                const pBtn = document.getElementById('voice-persistent-btn');
                const pLabel = document.getElementById('voice-persistent-label');
                if (pBtn) {
                    pBtn.classList.add('listening'); 
                    pBtn.innerHTML = '<i class="fas fa-spinner fa-spin" style="font-size:20px;"></i>';
                }
                if (pLabel) pLabel.textContent = 'PARSING AUDIO...';
            }

            const formData = new FormData();
            formData.append('audio', blob, 'order.webm');
            
            try {
                const res = await app.api('/parse-voice-audio', 'POST', formData);
                
                resetVoiceUI();
                
                if (res && res.success && res.items && res.items.length > 0) {
                    if (mode === 'persistent') {
                        window.parsedResult = {
                            items: res.items,
                            customer_name: res.customer_name,
                            payment_mode: res.payment_mode
                        };
                        
                        let html = '';
                        res.items.forEach(it => {
                            html += `
                            <div class="flex justify-between items-center py-2 border-b border-gray-100 last:border-0">
                                <div>
                                    <div class="font-medium text-gray-800">${window.esc(it.item_name)}</div>
                                    <div class="text-sm text-gray-500">Qty: ${window.esc(it.quantity)}</div>
                                </div>
                                <div class="font-semibold text-gray-900">₹${Number(it.total_price).toFixed(2)}</div>
                            </div>`;
                        });
                        document.getElementById('voice-review-items').innerHTML = html;
                        document.getElementById('voice-review-modal').classList.remove('hidden');
                    } else if (mode === 'quick') {
                        let addedAny = false;
                        res.items.forEach(parsedItem => {
                           const invItem = app.inventory.find(i => i.item_name.toLowerCase() === parsedItem.item_name.toLowerCase());
                           if (invItem) {
                               const existing = app.billItems.find(i => i.item.id === invItem.id);
                               if (existing) {
                                   existing.quantity += parseFloat(parsedItem.quantity);
                               } else {
                                   app.billItems.unshift({ item: invItem, quantity: parseFloat(parsedItem.quantity) });
                               }
                               addedAny = true;
                           }
                        });
                        if (addedAny) {
                            app.renderBill();
                            app.showToast('Items added to bill', 'success');
                            if (res.customer_name && res.customer_name !== 'Walk-in') {
                                document.getElementById('customer-name').value = res.customer_name;
                            }
                            if (res.payment_mode) {
                                document.getElementById('payment-mode').value = res.payment_mode;
                            }
                        } else {
                            app.showToast('Audio parsed, but no matching stock found', 'warning');
                        }
                    }
                } else {
                    app.showToast(res.message || 'Could not understand order audio', 'error');
                }
            } catch (err) {
                console.error('Audio upload error:', err);
                app.showToast('Error parsing audio. Please check network.', 'error');
                resetVoiceUI();
            } finally {
                if (mode === 'persistent') {
                    const pLabel = document.getElementById('voice-persistent-label');
                    if (pLabel) pLabel.textContent = 'TAP TO ADD BY VOICE';
                    const pBtn = document.getElementById('voice-persistent-btn');
                    if (pBtn) pBtn.innerHTML = '<i class="fas fa-microphone" style="font-size:20px;"></i>';
                }
            }
        }
"""
    new_lines = lines[:start_idx] + [new_script + '\n'] + lines[end_idx:]
    with open(filepath, 'w') as f:
        f.writelines(new_lines)
    print(f'Successfully updated {filepath}')
else:
    print('Failed to find start or end index')
