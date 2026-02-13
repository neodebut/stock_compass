// ChartStateManager.js - 全域狀態管理器
class ChartStateManager {
    constructor() {
        this.state = {
            crosshair: {
                index: null,
                timestamp: null,
                source: null,
                isLocked: false
            },
            viewport: {
                candleWidth: 10,
                visibleCount: 100,
                rightOffset: 0,
                dataRange: { start: 0, end: 0 }
            }
        };
        this.subscribers = new Map();
        this.data = [];
    }

    setData(data) {
        this.data = data;
        this.enforceRightAlignment();
    }

    // Rule 1: 設定十字線索引並廣播
    setCrosshairIndex(index, source = 'main') {
        if (index < 0 || index >= this.data.length) return;
        
        this.state.crosshair = {
            index,
            timestamp: this.data[index]?.time || Date.now(),
            source,
            isLocked: true
        };
        
        this.broadcast('crosshair:move', {
            index,
            timestamp: this.state.crosshair.timestamp
        });
    }

    clearCrosshair() {
        this.state.crosshair = {
            index: null,
            timestamp: null,
            source: null,
            isLocked: false
        };
        this.broadcast('crosshair:clear', {});
    }

    // Rule 2: 強制右對齊計算
    enforceRightAlignment() {
        if (!this.data || this.data.length === 0) return;

        const { candleWidth } = this.state.viewport;
        const containerWidth = this.getContainerWidth();
        
        // 計算最大可視數量
        const maxVisible = Math.floor(containerWidth / candleWidth);
        
        // 確保 end 永遠等於最新數據索引
        const endIndex = this.data.length - 1;
        const startIndex = Math.max(0, endIndex - maxVisible + 1);
        
        this.state.viewport.dataRange = { start: startIndex, end: endIndex };
        this.state.viewport.rightOffset = 0; // 零空白
        this.state.viewport.visibleCount = endIndex - startIndex + 1;
        
        this.broadcast('viewport:align', this.state.viewport);
    }

    // 縮放時的右對齊算法 (Zoom with Right Anchor)
    performZoom(focusX, scaleFactor, minWidth = 2, maxWidth = 50) {
        const oldWidth = this.state.viewport.candleWidth;
        const newWidth = Math.max(minWidth, Math.min(oldWidth * scaleFactor, maxWidth));
        
        // Rule 2: 以右邊界為錨點
        const containerWidth = this.getContainerWidth();
        const newVisibleCount = Math.floor(containerWidth / newWidth);
        
        const endIndex = this.data.length - 1;
        const startIndex = Math.max(0, endIndex - newVisibleCount + 1);
        
        this.state.viewport.candleWidth = newWidth;
        this.state.viewport.visibleCount = newVisibleCount;
        this.state.viewport.dataRange = { start: startIndex, end: endIndex };
        this.state.viewport.rightOffset = 0;
        
        this.broadcast('viewport:zoom', this.state.viewport);
    }

    // 平移時的邊界限制 (Pan Constraint)
    handlePan(deltaX) {
        const candleWidth = this.state.viewport.candleWidth;
        const dataLength = this.data.length;
        const containerWidth = this.getContainerWidth();
        
        // 計算理論新位置
        const newOffset = this.state.viewport.rightOffset + deltaX;
        
        // Rule 2: 嚴格限制向右平移（禁止出現空白）
        // 最大向右平移量為 0（對齊右邊界）
        const clampedOffset = Math.min(0, newOffset);
        
        // 向左平移限制（不能超過最早數據）
        const maxLeftOffset = -(dataLength * candleWidth - containerWidth);
        const finalOffset = Math.max(maxLeftOffset, clampedOffset);
        
        this.state.viewport.rightOffset = finalOffset;
        
        // 更新 dataRange
        const endIndex = dataLength - 1;
        const startIndex = Math.max(0, Math.floor(-finalOffset / candleWidth));
        this.state.viewport.dataRange = { start: startIndex, end: endIndex };
        
        this.broadcast('viewport:pan', this.state.viewport);
    }

    getContainerWidth() {
        // 預設值，實際應在初始化時設定
        return this.containerWidth || 800;
    }

    setContainerWidth(width) {
        this.containerWidth = width;
        this.enforceRightAlignment();
    }

    // 訂閱/廣播機制
    subscribe(event, callback) {
        if (!this.subscribers.has(event)) {
            this.subscribers.set(event, []);
        }
        this.subscribers.get(event).push(callback);
    }

    broadcast(event, payload) {
        const callbacks = this.subscribers.get(event) || [];
        callbacks.forEach(cb => {
            try {
                cb(payload);
            } catch (e) {
                console.error('Subscriber error:', e);
            }
        });
    }

    // 獲取當前狀態
    getState() {
        return { ...this.state };
    }

    getCrosshairIndex() {
        return this.state.crosshair.index;
    }

    getViewport() {
        return { ...this.state.viewport };
    }

    getVisibleData() {
        const { start, end } = this.state.viewport.dataRange;
        return this.data.slice(start, end + 1);
    }

    indexToX(index) {
        const { candleWidth, dataRange, rightOffset } = this.state.viewport;
        const containerWidth = this.getContainerWidth();
        const endIndex = this.data.length - 1;
        
        // 計算相對於右邊界的 X 座標
        const offsetFromRight = endIndex - index;
        return containerWidth - (offsetFromRight * candleWidth) - (candleWidth / 2) + rightOffset;
    }

    xToIndex(x) {
        const { candleWidth } = this.state.viewport;
        const containerWidth = this.getContainerWidth();
        const endIndex = this.data.length - 1;
        
        // 計算相對於右邊界的偏移
        const offsetFromRight = (containerWidth - x) / candleWidth;
        const index = Math.round(endIndex - offsetFromRight + 0.5);
        
        return Math.max(0, Math.min(index, this.data.length - 1));
    }
}

// 導出為全域變數
window.ChartStateManager = ChartStateManager;
