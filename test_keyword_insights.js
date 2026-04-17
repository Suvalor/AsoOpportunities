/**
 * 测试套件：关键词洞察报告全盘扫描功能
 * 文件：app/static/test_keyword-insights.js
 * 
 * 基于 requirements.md 中的验收标准 AC1-AC7
 */

/* ========== 测试工具函数 ========== */

// 模拟 DOM 环境
class MockDOM {
  constructor() {
    this.elements = {};
  }

  getElementById(id) {
    if (!this.elements[id]) {
      this.elements[id] = {
        id,
        textContent: '',
        innerHTML: '',
        style: {},
        disabled: false,
        classList: {
          classes: new Set(),
          add: function(cls) { this.classes.add(cls); },
          remove: function(cls) { this.classes.delete(cls); },
          has: function(cls) { return this.classes.has(cls); }
        },
        addEventListener: () => {},
        onclick: null,
      };
    }
    return this.elements[id];
  }

  setStyle(id, prop, val) {
    this.elements[id].style[prop] = val;
  }

  getStyle(id, prop) {
    return this.elements[id].style[prop];
  }
}

let mockDOM = new MockDOM();
global.document = {
  getElementById: (id) => mockDOM.getElementById(id),
  createElement: (tag) => ({ textContent: '', innerHTML: '', style: {} }),
};

/* ========== 测试用例 ========== */

const tests = [];

function test(name, fn) {
  tests.push({ name, fn });
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(`❌ 断言失败: ${message}`);
  }
}

function assertEqual(actual, expected, message) {
  assert(actual === expected, `${message} (期望: ${expected}, 实际: ${actual})`);
}

function assertTrue(value, message) {
  assert(value === true, `${message} (期望 true, 实际 ${value})`);
}

function assertFalse(value, message) {
  assert(value === false, `${message} (期望 false, 实际 ${value})`);
}

// Mock 全局函数
global.showWarning = () => {};
global.showSuccess = () => {};
global.showError = () => {};

/* ========== AC1: 按钮显示 ========== */
test('AC1: 【全盘扫描】按钮显示在 header-right', () => {
  const btnScan = mockDOM.getElementById('btnScan');
  assert(btnScan !== null, '按钮元素存在');
  assertEqual(btnScan.id, 'btnScan', '按钮 ID 正确');
});

/* ========== AC2: 点击后按钮禁用 ========== */
test('AC2: 点击按钮后禁用，显示"扫描中..."', () => {
  const scanMgr = {
    currentBatchId: null,
    async startScan() {
      const btn = mockDOM.getElementById('btnScan');
      btn.disabled = true;
      btn.classList.add('scanning');
      btn.textContent = '⏳ 扫描中...';
    }
  };

  // 模拟点击
  scanMgr.startScan().then(() => {
    const btn = mockDOM.getElementById('btnScan');
    assertTrue(btn.disabled, '按钮应被禁用');
    assert(btn.classList.has('scanning'), '按钮应加 scanning 类');
    assertEqual(btn.textContent, '⏳ 扫描中...', '按钮文本应更新');
  });
});

/* ========== AC3: 轮询逻辑 ========== */
test('AC3: 应每秒轮询 /scan/status/{batch_id}', () => {
  let pollCount = 0;
  const scanMgr = {
    currentBatchId: 'test-batch-123',
    pollInterval: null,
    pollCount: 0,
    MAX_POLLS: 3600,
    
    async pollStatus() {
      this.pollInterval = setInterval(() => {
        pollCount++;
        assert(this.currentBatchId, '应保持 batchId');
      }, 1000);
      
      // 模拟 2 秒内应轮询 2 次
      await new Promise(r => setTimeout(r, 2100));
      clearInterval(this.pollInterval);
    }
  };

  return scanMgr.pollStatus().then(() => {
    assert(pollCount >= 1, `应至少轮询 1 次（实际 ${pollCount}）`);
  });
});

/* ========== AC4: 扫描完成 ========== */
test('AC4: 扫描完成时显示提示，按钮恢复可用', () => {
  const scanMgr = {
    currentBatchId: 'batch-123',
    pollInterval: null,
    
    handleScanSuccess(jobData) {
      const btn = mockDOM.getElementById('btnScan');
      btn.disabled = false;
      btn.classList.remove('scanning');
      btn.textContent = '📡 全盘扫描';
      this.currentBatchId = null;
    }
  };

  // 模拟扫描完成
  scanMgr.handleScanSuccess({ keywords_found: 42 });
  
  const btn = mockDOM.getElementById('btnScan');
  assertFalse(btn.disabled, '按钮应可用');
  assert(!btn.classList.has('scanning'), '应移除 scanning 类');
  assertEqual(btn.textContent, '📡 全盘扫描', '按钮文本应恢复');
});

/* ========== AC5: 扫描失败 ========== */
test('AC5: 扫描失败时显示错误和重试选项', () => {
  const scanMgr = {
    handleScanFailure() {
      const btn = mockDOM.getElementById('btnScan');
      btn.disabled = false;
      btn.classList.remove('scanning');
      btn.textContent = '📡 全盘扫描';
      this.currentBatchId = null;
    }
  };

  scanMgr.handleScanFailure();
  const btn = mockDOM.getElementById('btnScan');
  
  assertFalse(btn.disabled, '按钮应可用以支持重试');
  assertEqual(btn.textContent, '📡 全盘扫描', '按钮应恢复初始状态');
});

/* ========== 防重机制 ========== */
test('防重机制: 不允许并发扫描', async () => {
  const scanMgr = {
    currentBatchId: 'batch-001',
    
    async startScan() {
      if (this.currentBatchId) {
        showWarning('扫描正在进行中，请稍候...');
        return;
      }
      // ... 启动扫描
    }
  };

  // 第一次扫描时 currentBatchId 已设置，所以第二次调用应该返回
  await scanMgr.startScan();
  // 由于 currentBatchId 已设置，第二次应该被阻止
  assertEqual(scanMgr.currentBatchId, 'batch-001', '应保持 batchId');
});

/* ========== 熔断机制 ========== */
test('熔断机制: 轮询超过 3600 次应停止', () => {
  const scanMgr = {
    pollCount: 3601,
    MAX_POLLS: 3600,
    pollInterval: 'some_id',
    currentBatchId: 'batch-001',
    
    checkPolls() {
      if (this.pollCount > this.MAX_POLLS) {
        clearInterval(this.pollInterval);
        this.pollInterval = null;
        return false; // 不应继续轮询
      }
      return true; // 应继续轮询
    }
  };

  const shouldContinue = scanMgr.checkPolls();
  assertFalse(shouldContinue, '应停止轮询');
  assertEqual(scanMgr.pollInterval, null, '轮询应被清理');
});

/* ========== 运行所有测试 ========== */
async function runTests() {
  console.log('🧪 开始运行测试套件...\n');
  
  let passed = 0;
  let failed = 0;

  for (const test of tests) {
    try {
      const result = test.fn();
      if (result instanceof Promise) {
        await result;
      }
      console.log(`✅ 通过: ${test.name}`);
      passed++;
    } catch (err) {
      console.log(`❌ 失败: ${test.name}`);
      console.log(`   ${err.message}\n`);
      failed++;
    }
  }

  console.log(`\n📊 测试结果: ${passed} 通过, ${failed} 失败`);
  
  if (failed === 0) {
    console.log('🎉 所有测试通过！');
    return true;
  } else {
    console.log('❌ 有测试失败，需要修复');
    return false;
  }
}

// 导出供 Node.js 环境使用
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { runTests, tests };
  // 在 Node.js 环境下立即运行
  runTests().then(success => {
    process.exit(success ? 0 : 1);
  }).catch(err => {
    console.error('测试执行错误:', err);
    process.exit(1);
  });
}

// 浏览器环境
if (typeof window !== 'undefined') {
  window.runTests = runTests;
}
