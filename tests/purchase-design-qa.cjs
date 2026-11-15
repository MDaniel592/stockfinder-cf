const {chromium}=require('@playwright/test');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(20000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));const base='http://127.0.0.1:8800';
 await page.goto(base+'/configurador',{waitUntil:'networkidle'});
 let button=page.locator('.purchase-action');assert(await button.isDisabled());assert.equal(await button.getAttribute('aria-busy'),'false');assert.equal(await button.evaluate(e=>getComputedStyle(e).cursor),'not-allowed');
 const slugs={cpu:'amd-ryzen-7-9800x3d',gpu:'nvidia-geforce-rtx-5070',ram:'corsair-vengeance-32gb-ddr5-6000-cl30',storage:'samsung-990-pro-2tb',psu:'corsair-rm850e-2025',case:'fractal-north-charcoal-black-tg-dark',motherboard:'asus-tuf-gaming-b850-plus-wifi',cooler:'noctua-nh-d15-g2'};
 const parts={};for(const [slot,slug] of Object.entries(slugs)){const r=await page.request.get(base+'/api/v1/builder/products/'+slug);assert(r.ok());parts[slot]=(await r.json()).data;if(parts[slot].min_price_cents===null){const options=await (await page.request.get(base+'/api/v1/builder/options?category='+slot)).json();parts[slot]=options.data.find(p=>p.min_price_cents!==null);assert(parts[slot]);}}
 for(const slot of Object.keys(parts)){const offers=(await (await page.request.get(base+'/api/v1/builder/products/'+parts[slot].slug+'/offers')).json()).data;if(!offers.some(o=>o.in_stock&&Date.now()-Date.parse(o.last_checked_at)<=86400000)){const options=(await (await page.request.get(base+'/api/v1/builder/options?category='+slot)).json()).data;for(const p of options){const list=(await (await page.request.get(base+'/api/v1/builder/products/'+p.slug+'/offers')).json()).data;if(list.some(o=>o.in_stock&&Date.now()-Date.parse(o.last_checked_at)<=86400000)){parts[slot]=p;break;}}}}const now=new Date().toISOString();const items=Object.entries(parts).map(([slot,p])=>({slot,product_id:p.id,name:p.name,price_cents:p.min_price_cents,last_checked_at:p.last_checked_at}));
 const snapshot={id:'snapshot-qa',created_at:now,items,total_known_cents:items.reduce((n,p)=>n+(p.price_cents??0),0),priced_items:items.filter(p=>p.price_cents!==null).length,total_items:items.length};
 await page.evaluate(({now,parts,snapshot})=>localStorage.setItem('stockfinder.pc-builds.v1',JSON.stringify({schema_version:1,builds:[{id:'purchase-qa',name:'Mi PC',created_at:now,updated_at:now,parts,snapshots:[snapshot]}]})),{now,parts,snapshot});
 await page.reload({waitUntil:'networkidle'});await page.getByRole('button',{name:'Cargar lista',exact:true}).click();
 await page.route('**/api/v1/builder/products/*/offers',async route=>{await new Promise(resolve=>setTimeout(resolve,400));await route.continue();});
 await page.getByRole('button',{name:'Comparar compra por tiendas',exact:true}).click();
 button=page.locator('.purchase-action');assert.equal(await button.getAttribute('aria-busy'),'true');assert.equal(await button.evaluate(e=>getComputedStyle(e).cursor),'wait');
 await page.locator('.purchase-plans article').first().waitFor();assert.equal(await button.getAttribute('aria-busy'),'false');
 assert(!(await page.locator('.purchase-costs').getAttribute('open')));assert.equal(await page.locator('.purchase-option-total').first().innerText(),'Total con gastos\nPendiente');
 await page.evaluate(()=>document.activeElement?.blur());await page.locator('.purchase-planner').screenshot({path:'artifacts/purchase-design-desktop.png'});
 await page.locator('.purchase-costs > summary').click();const inputs=page.locator('.purchase-cost-grid input');
 for(let i=0;i<await inputs.count();i++)await inputs.nth(i).fill('0');
 const complete=await page.locator('.purchase-option-total').allTextContents();assert(complete.some(text=>!text.includes('Pendiente')));
 await page.getByLabel('Destino',{exact:true}).selectOption('canarias');assert((await page.locator('.purchase-option-total').allTextContents()).every(text=>text.includes('Pendiente')));
 await page.getByLabel('Destino',{exact:true}).selectOption('peninsula');assert((await page.locator('.purchase-option-total').allTextContents()).some(text=>!text.includes('Pendiente')));
 await page.locator('.purchase-breakdown > summary').click();assert((await page.locator('.purchase-shop a').count())>0);
 await page.evaluate(()=>document.activeElement?.blur());await page.locator('.purchase-planner').screenshot({path:'artifacts/purchase-design-desktop-expanded.png'});
 await page.setViewportSize({width:390,height:8000});await page.evaluate(()=>{document.activeElement?.blur();scrollTo(0,0);});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.evaluate(()=>document.activeElement?.blur());await page.locator('.purchase-planner').screenshot({path:'artifacts/purchase-design-mobile.png'});
 await page.locator('.purchase-costs > summary').click();await page.locator('.purchase-breakdown > summary').click();
 await page.evaluate(()=>document.activeElement?.blur());await page.locator('.purchase-planner').screenshot({path:'artifacts/purchase-design-mobile-compact.png'});
 const radios=page.getByRole('radio');if(await radios.count()>1){await radios.nth(1).check();assert(await radios.nth(1).isChecked());}
 assert.deepEqual(errors,[]);await browser.close();console.log('Purchase QA passed: empty/busy cursor, 8 parts, totals pending, explicit zero costs, destination isolation, shop links, selectable plans and 390px no overflow.');
})().catch(e=>{console.error(e);process.exit(1)});
