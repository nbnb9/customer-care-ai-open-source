# 步骤 41.5 可点击原型

[`careops-console.html`](careops-console.html) 是不连接 API 的产品化方向原型，用于验证：

- 客户、坐席、主管三个独立产品表面；
- 坐席“队列—会话/任务—客户/订单上下文”三栏结构；
- 证据、政策、客户确认、坐席审批和外部状态的语义；
- 设计 token、密度、窄屏回流和基础键盘路径。

它不是生产页面，不执行权限检查，也不证明 Shopify、身份或外部写入契约。不要把原型里的
隐藏按钮当作授权控制。

从仓库根目录本地查看：

```bash
python3 -m http.server 4173 --directory docs/product/prototypes
```

然后打开 <http://127.0.0.1:4173/careops-console.html>。
