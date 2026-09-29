// SPDX-License-Identifier: AGPL-3.0-or-later
// Perspective projection of actual 3D cube geometry, drawn with Canvas 2D.
// No CDN, GPU requirement, telemetry, or external rendering dependency.
// Ending value vs simple DCA at the same budget: blue behind, white within ±1%, orange ahead.
export const COMPARISON_BANDS=[
  {rgb:[44,98,184],label:'20%+ behind'},{rgb:[74,138,221],label:'5–20% behind'},{rgb:[150,190,235],label:'1–5% behind'},
  {rgb:[242,242,236],label:'within ±1%'},
  {rgb:[246,190,140],label:'1–5% ahead'},{rgb:[236,140,70],label:'5–20% ahead'},{rgb:[216,90,40],label:'20%+ ahead'}];
export function comparisonBand(v){
  if(v==null||!Number.isFinite(v))return -1;
  if(v>-1&&v<1)return 3;
  if(v<0)return v<=-20?0:v<=-5?1:2;
  return v>=20?6:v>=5?5:4;
}
export function vsDcaText(v){if(v==null||!Number.isFinite(v))return 'vs simple DCA unavailable';return `${v>=0?'+':'−'}${Math.abs(v).toFixed(2)}% vs simple DCA · ${v>-1&&v<1?'about even':v>0?'ahead':'behind'}`;}
export class CubeSpace {
  constructor(canvas, onSelect) {
    this.canvas = canvas; this.ctx = canvas.getContext('2d'); this.onSelect = onSelect;
    this.yaw = -.65; this.pitch = .43; this.zoom = 1; this.cells = [];
    this.metric = 'vs_simple_dca_pct'; this.slice = 'all'; this.polygons = [];
    this.tooltip = document.querySelector('#cube-tooltip'); this.selected = null;
    new ResizeObserver(() => this.draw()).observe(canvas);
    canvas.addEventListener('pointerdown', e => { this.drag = {x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,moved:false}; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', e => {
      if (this.drag) {
        const dx=e.clientX-this.drag.x, dy=e.clientY-this.drag.y;
        this.drag.moved ||= Math.hypot(e.clientX-this.drag.startX,e.clientY-this.drag.startY)>4;
        if(this.drag.moved){this.yaw+=dx*.008;this.pitch=Math.max(-.9,Math.min(1.15,this.pitch+dy*.006));}
        this.drag.x=e.clientX;this.drag.y=e.clientY;this.tooltip.hidden=true;this.draw();
      } else this.hover(e);
    });
    canvas.addEventListener('pointerup', e => { if(this.drag&&!this.drag.moved){const hit=this.hit(e);if(hit)this.onSelect(hit);} this.drag=null; });
    canvas.addEventListener('pointercancel',()=>{this.drag=null;});
    canvas.addEventListener('pointerleave',()=>{this.tooltip.hidden=true;});
    canvas.addEventListener('wheel',e=>{e.preventDefault();this.zoom=Math.max(.65,Math.min(1.7,this.zoom-e.deltaY*.001));this.draw();},{passive:false});
    canvas.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();this.yaw+=e.key==='ArrowLeft'?-.12:e.key==='ArrowRight'?.12:0;this.pitch=Math.max(-.9,Math.min(1.15,this.pitch+(e.key==='ArrowUp'?.08:e.key==='ArrowDown'?-.08:0)));this.draw();}});
  }
  setData(cells,selected){this.cells=cells;this.selected=selected;this.axes=['dca_pct','va_pct','capture_pct'].map(k=>[...new Set(cells.map(c=>c[k]))].sort((a,b)=>a-b));this.draw();}
  reset(){this.yaw=-.65;this.pitch=.43;this.zoom=1;this.draw();}
  project([x,y,z]) {
    const x1=x*Math.cos(this.yaw)+z*Math.sin(this.yaw),z1=-x*Math.sin(this.yaw)+z*Math.cos(this.yaw);
    const y1=y*Math.cos(this.pitch)-z1*Math.sin(this.pitch), z2=y*Math.sin(this.pitch)+z1*Math.cos(this.pitch);
    const scale=this.unit*10/(10+z2);
    return {x:this.w*.5+x1*scale,y:this.h*.55-y1*scale,z:z2};
  }
  draw(){
    const rect=this.canvas.getBoundingClientRect();this.w=rect.width;this.h=rect.height;if(!this.w||!this.h)return;
    const dpr=Math.min(devicePixelRatio||1,2);this.canvas.width=Math.round(this.w*dpr);this.canvas.height=Math.round(this.h*dpr);
    const ctx=this.ctx;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,this.w,this.h);
    // Axis, grid, and edge colors follow the active theme in style.css; data colors do not.
    const css=getComputedStyle(this.canvas),themed=name=>css.getPropertyValue(`--cube-${name}`).trim();
    this.unit=Math.min(this.w/10.6,this.h/11)*this.zoom;this.polygons=[];if(!this.cells.length)return;
    const pos=(idx,n)=>n===1?0:(idx/(n-1)-.5)*3.7;
    const stroke=(a,b,color,width=1)=>{const p=this.project(a),q=this.project(b);ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.strokeStyle=color;ctx.lineWidth=width;ctx.stroke();};
    for(let i=0;i<6;i++){const t=-2.4+i*.96;stroke([t,-2.45,-2.4],[t,-2.45,2.4],themed('floor'));stroke([-2.4,-2.45,t],[2.4,-2.45,t],themed('floor'));}
    stroke([-2.5,-2.45,-2.5],[2.8,-2.45,-2.5],themed('x'),1.4);
    stroke([-2.5,-2.45,-2.5],[-2.5,2.8,-2.5],themed('y'),1.4);
    stroke([-2.5,-2.45,-2.5],[-2.5,-2.45,2.8],themed('z'),1.4);
    const values=this.cells.map(c=>c[this.metric]);const lo=Math.min(...values),hi=Math.max(...values);
    const verts=[[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]];
    const faces=[[0,1,2,3],[4,7,6,5],[0,4,5,1],[3,2,6,7],[1,5,6,2],[0,3,7,4]];
    const polygons=[];
    for(const cell of this.cells){
      if(this.slice!=='all'&&Math.abs(cell.capture_pct-Number(this.slice))>1e-8)continue;
      const center=['dca_pct','va_pct','capture_pct'].map((k,i)=>pos(this.axes[i].indexOf(cell[k]),this.axes[i].length));
      const size=.25; const v=verts.map(p=>this.project(p.map((c,i)=>center[i]+c*size)));
      let rgb;
      if(this.metric==='vs_simple_dca_pct'){const band=comparisonBand(cell.vs_simple_dca_pct);rgb=band<0?[110,118,122]:COMPARISON_BANDS[band].rgb;}
      else{
        let t=hi===lo?.5:(cell[this.metric]-lo)/(hi-lo);
        // Less severe (closer to zero, or smaller positive magnitude) drawdown is brighter.
        if(this.metric==='max_drawdown_pct'&&lo>=0)t=1-t;
        rgb=[Math.round(53+t*66),Math.round(81+t*142),Math.round(106+t*68)];
      }
      const selected=this.selected&&['dca_pct','va_pct','capture_pct'].every(k=>Math.abs(cell[k]-this.selected[k])<1e-8);
      faces.forEach((face,i)=>{const points=face.map(n=>v[n]);polygons.push({points,depth:points.reduce((s,p)=>s+p.z,0)/4,cell,selected,fill:`rgb(${rgb.map(c=>Math.round(c*[.68,.78,.57,1,.88,.71][i])).join(',')})`});});
    }
    polygons.sort((a,b)=>b.depth-a.depth);
    for(const face of polygons){ctx.beginPath();face.points.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.closePath();ctx.fillStyle=face.fill;ctx.fill();ctx.strokeStyle=face.selected?themed('selected'):themed('edge');ctx.lineWidth=face.selected?1.5:.55;ctx.stroke();}
    this.polygons=polygons;
    const text=(p,label,color,align='center')=>{const q=this.project(p);ctx.font='10px ui-monospace,monospace';ctx.fillStyle=color;ctx.textAlign=align;ctx.fillText(label,q.x,q.y);};
    text([3.1,-2.7,-2.5],'DCA %',themed('x-label'));text([-2.5,3.2,-2.5],'DVA % / day',themed('y-label'));text([-2.5,-2.85,3.1],'CAPTURE %',themed('z-label'));
    this.axes.forEach((values,i)=>values.forEach((value,j)=>{const p=[-2.5,-2.45,-2.5];p[i]=pos(j,values.length);if(i===0)p[1]-=.3;else if(i===1)p[0]-=.35;else p[0]-=.33;text(p,String(value),themed('tick'));}));
  }
  hit(e){const r=this.canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;for(let i=this.polygons.length-1;i>=0;i--){const face=this.polygons[i];let inside=false;for(let j=0,k=face.points.length-1;j<face.points.length;k=j++){const a=face.points[j],b=face.points[k];if(((a.y>y)!==(b.y>y))&&(x<(b.x-a.x)*(y-a.y)/(b.y-a.y)+a.x))inside=!inside;}if(inside)return face.cell;}return null;}
  hover(e){const cell=this.hit(e);if(!cell){this.tooltip.hidden=true;return;}const rect=this.canvas.getBoundingClientRect();this.tooltip.textContent=`DCA ${cell.dca_pct}% · DVA ${cell.va_pct}%\nCapture ${cell.capture_pct}%\nReturn ${cell.total_return_pct.toFixed(2)}%\n${vsDcaText(cell.vs_simple_dca_pct)}\nDrawdown ${cell.max_drawdown_pct.toFixed(2)}%\nClick to inspect this scenario`;this.tooltip.hidden=false;this.tooltip.style.left=`${Math.max(0,Math.min(e.clientX-rect.left+12,this.w-250))}px`;this.tooltip.style.top=`${Math.min(e.clientY-rect.top+12,this.h-155)}px`;}
}
