document.addEventListener('DOMContentLoaded', () => {
    // --- DOM 元素 ---
    const urlEntry = document.getElementById('url-entry');
    const hostsEntry = document.getElementById('hosts-entry');
    const btnDownload = document.getElementById('btn-download');
    const btnWrite = document.getElementById('btn-write');
    const btnUndo = document.getElementById('btn-undo');
    const btnClear = document.getElementById('btn-clear');
    const btnBrowse = document.getElementById('btn-browse');
    const outputBox = document.getElementById('output-box');
    const progressBar = document.getElementById('progress-bar');
    const adminWarningBanner = document.getElementById('admin-warning-banner');
    const appWrapper = document.querySelector('.app-wrapper');

    // --- 應用程式狀態 ---
    let isProcessing = false;

    // --- 核心函式 ---

    function logOutput(message, tag = 'info') {
        const emptyState = outputBox.querySelector('.empty-state');
        if (emptyState) {
            emptyState.style.opacity = '0';
            setTimeout(() => emptyState.remove(), 300);
        }

        const iconPaths = {
            success: '圖示/成功.svg',
            error: '圖示/失敗.svg',
            info: '圖示/資訊.svg',
            warning: '圖示/紅色警告.svg'
        };
        const iconSrc = iconPaths[tag] || iconPaths.info;

        const logEntry = document.createElement('div');
        logEntry.className = `log ${tag}`;
        logEntry.innerHTML = `
            <div class="log-icon"><img src="${iconSrc}" alt="${tag}" class="icon-img"></div>
            <div class="log-content">
                <div class="log-message">${message.replace(/\n/g, '<br>')}</div>
                <div class="log-timestamp">${new Date().toLocaleTimeString('en-GB')}</div>
            </div>`;
        outputBox.appendChild(logEntry);
        outputBox.scrollTop = outputBox.scrollHeight;
    }

    function setButtonsDisabled(disabled) {
        isProcessing = disabled;
        btnDownload.disabled = disabled;
        btnUndo.disabled = disabled;
        btnBrowse.disabled = disabled;

        if (disabled) {
            btnWrite.disabled = true;
        } else {
            btnWrite.disabled = btnWrite.dataset.enabled !== 'true';
        }
    }

    function setProgressActive(active) {
        progressBar.classList.toggle('active', active);
    }

    async function performAction(startMessage, eelFunction, actionType = 'generic') {
        if (isProcessing) return;

        setButtonsDisabled(true);
        setProgressActive(true);
        logOutput(startMessage, 'info');

        try {
            // eelFunction 必須回傳一個 Promise（來自 eel.xxx()()）
            const result = await eelFunction();

            console.log('從 Python 收到:', result);

            if (result && typeof result.tag === 'string' && typeof result.message === 'string') {
                logOutput(result.message, result.tag);

                if (actionType === 'download' && result.tag === 'success') {
                    btnWrite.dataset.enabled = 'true';
                    btnWrite.title = '將下載的內容寫入 Hosts 檔案';
                    btnWrite.classList.add('glowing');
                } else {
                    btnWrite.classList.remove('glowing');
                }
            } else {
                throw new Error('後端返回的資料格式不正確或無效。');
            }
        } catch (error) {
            console.error('performAction 失敗:', error);
            logOutput(`前端操作失敗: ${error.message}`, 'error');
            btnWrite.classList.remove('glowing');
        } finally {
            setProgressActive(false);
            setButtonsDisabled(false);
        }
    }

    // --- 事件監聽器 ---

    btnDownload.addEventListener('click', () => {
        const url = urlEntry.value.trim();
        if (!url) {
            logOutput('請輸入來源 URL。', 'error');
            return;
        }
        btnWrite.dataset.enabled = 'false'; // 重置寫入按鈕狀態
        // 關鍵修正：Eel 回傳值要用 ()() 取得
        performAction(
            `正在從 ${url} 下載...`,
            () => eel.download_domains_py(url)(),
            'download'
        );
    });

    btnWrite.addEventListener('click', () => {
        const path = hostsEntry.value.trim();
        if (!path) {
            logOutput('請提供 Hosts 路徑。', 'error');
            return;
        }
        // 關鍵修正：同步函式也要用 ()() 來拿回傳值
        performAction(
            `正在寫入 hosts 檔案到 ${path}...`,
            () => eel.write_hosts_py(path)()
        );
    });

    btnUndo.addEventListener('click', () => {
        const path = hostsEntry.value.trim();
        if (!path) {
            logOutput('請提供 Hosts 路徑。', 'error');
            return;
        }
        // 關鍵修正：()()
        performAction(
            `正在從備份還原 ${path}...`,
            () => eel.undo_hosts_py(path)()
        );
    });

    btnBrowse.addEventListener('click', async () => {
        if (isProcessing) return;
        setButtonsDisabled(true);
        logOutput('正在開啟檔案瀏覽器...', 'info');
        try {
            const path = await eel.browse_for_hosts_file_py()();
            if (path) {
                hostsEntry.value = path;
                logOutput(`已選擇檔案: ${path}`, 'success');
            } else {
                logOutput('未選擇任何檔案。', 'info');
            }
        } catch (error) {
            logOutput(`瀏覽檔案時發生錯誤: ${error}`, 'error');
        } finally {
            setButtonsDisabled(false);
        }
    });

    btnClear.addEventListener('click', () => {
        outputBox.innerHTML = `
            <div class="empty-state">
                <img src="圖示/日誌空狀態.svg" alt="日誌空狀態" class="icon-img-large">
                <p>日誌已清除</p>
                <span>等待新的操作...</span>
            </div>`;
    });

    // --- 初始化函式 ---
    async function initialize() {
        try {
            if (typeof eel === 'undefined') {
                throw new Error('Eel.js 未能載入，無法與後端通訊。');
            }

            const [isAdmin, hostsPath] = await Promise.all([
                eel.is_admin_py()(),
                eel.get_hosts_path_py()()
            ]);

            if (!isAdmin) {
                adminWarningBanner.style.display = 'flex';
                logOutput('警告：未使用管理員權限執行，寫入或還原操作可能會失敗。', 'warning');
            }

            if (hostsPath) {
                hostsEntry.value = hostsPath;
                logOutput(`成功自動偵測到 Hosts 路徑: ${hostsPath}`, 'success');
            } else {
                logOutput('未能自動獲取 Hosts 路徑，請手動指定。', 'warning');
            }

        } catch (error) {
            logOutput(`應用程式初始化失敗: ${error.message}`, 'error');
            console.error('初始化失敗:', error);
        } finally {
            if (appWrapper) appWrapper.style.opacity = '1';
        }
    }

    initialize();
});
