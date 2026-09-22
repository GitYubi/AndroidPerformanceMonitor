import { createTreeDevice } from './hierarchy.mjs';
try {
  const tree = await createTreeDevice(process.argv[2]).read();
  console.log(JSON.stringify(tree));
} catch {
  console.log(JSON.stringify({ error: '无法读取控件树，请确认设备已授权且当前界面可访问' }));
  process.exitCode = 1;
}
