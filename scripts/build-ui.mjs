import { build } from 'esbuild';
import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';
import { icons as systemIcons } from '@awesome.me/webawesome/dist/components/icon/library.system.js';
const entries = [['interface.js','lastmile-ui.js'],['story.js','lastmile-story.js']];
const results = [];
for(const [entry, name] of entries) results.push({name, result:await build({entryPoints:['web/components/'+entry], bundle:true, minify:true, format:'esm', target:'es2022', legalComments:'inline', metafile:true, write:false})});
const packages = new Set();
for (const file of new Set(results.flatMap(({result})=>Object.keys(result.metafile.inputs)))) {
  const marker = file.lastIndexOf('node_modules/');
  if (marker < 0) continue;
  const pieces = file.slice(marker + 13).split('/');
  packages.add(file.slice(0, marker + 13) + pieces.slice(0, pieces[0].startsWith('@') ? 2 : 1).join('/'));
}
const notices = [];
for (const root of [...packages].sort()) {
  const metadata = JSON.parse(await readFile(root + '/package.json', 'utf8'));
  const file = (await readdir(root)).find(name => /^licen[cs]e(?:\.|$)/i.test(name));
  if (!file) throw new Error(`Missing licence notice for ${metadata.name}`);
  notices.push(`${metadata.name} ${metadata.version}\n${await readFile(root + '/' + file, 'utf8')}`);
  if(metadata.name === '@dagrejs/dagre') notices.push(await readFile(root+'/dist/dagre.esm.js.LEGAL.txt','utf8'));
}
const licences = notices.join('\n\n').replaceAll('\r\n','\n');
// Preserve the upstream SVG attribution and serve system icons locally, since
// wa-icon fetches its SVG and the app's connect/default CSP is same-origin.
for (const [variant, icons] of Object.entries(systemIcons)) {
  for (const [name, svg] of Object.entries(icons)) {
    const filename = `web/vendor/components/icons/${variant}/${name}.svg`;
    if (process.argv.includes('--check')) {
      if ((await readFile(filename, 'utf8')).replaceAll('\r\n', '\n') !== svg.replaceAll('\r\n', '\n')) throw new Error(`Stale UI icon: ${filename}`);
    } else {
      await mkdir(`web/vendor/components/icons/${variant}`, {recursive:true});
      await writeFile(filename, svg);
    }
  }
}
if (process.argv.includes('--check')) {
  for(const {name,result} of results) if (!(await readFile('web/vendor/components/'+name)).equals(Buffer.from(result.outputFiles[0].contents))) throw new Error('UI bundle is stale: '+name);
  if ((await readFile('web/vendor/components/LICENSES.txt', 'utf8')).replaceAll('\r\n','\n') !== licences) throw new Error('UI licence notices are stale.');
} else {
  await mkdir('web/vendor/components', {recursive:true});
  for(const {name,result} of results) await writeFile('web/vendor/components/'+name,result.outputFiles[0].contents);
  await writeFile('web/vendor/components/LICENSES.txt', licences);
}
for(const {name,result} of results){const content=result.outputFiles[0].contents;console.log(`${name}: ${content.length} bytes, ${gzipSync(content).length} bytes gzip`);}
