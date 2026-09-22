import test from 'node:test';
import assert from 'node:assert/strict';
import {parseHierarchy,resolveSelector,selectorFor} from './hierarchy.mjs';
const node = (extra='') => `<node package="com.android.settings" class="android.widget.Switch" resource-id="app:id/switch" enabled="true" bounds="[0,0][100,100]" ${extra}/>`;
test('password values are removed and checked state does not split page identity',()=>{
 const a=parseHierarchy(`<hierarchy>${node('password="true" text="secret" checked="true"')}</hierarchy>`);
 const b=parseHierarchy(`<hierarchy>${node('password="true" text="secret" checked="false"')}</hierarchy>`);
 assert.equal(a.nodes[0].text,'');assert.equal(a.fingerprint,b.fingerprint);
});
test('ambiguous selectors fail rather than clicking arbitrary coordinates',()=>{
 const tree=parseHierarchy(`<hierarchy>${node()}${node()}</hierarchy>`);
 assert.throws(()=>resolveSelector(tree,selectorFor(tree.nodes[0])));
 assert.throws(()=>resolveSelector(tree,{}));
});
test('malformed XML is rejected',()=>assert.throws(()=>parseHierarchy('<hierarchy>')));
