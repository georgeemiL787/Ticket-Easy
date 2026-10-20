(function(){
var deck=document.getElementById('deck'),sl=[].slice.call(deck.querySelectorAll('.slide')),cur=0;
var tpl=document.getElementById('dia');
[].forEach.call(deck.querySelectorAll('.camstage'),function(st){st.appendChild(tpl.content.cloneNode(true))});
function Cam(el){var st=el.querySelector('.camstage'),P=JSON.parse(el.dataset.p),s=1,tx=0,ty=0,key=el.dataset.start,bar=el.previousElementSibling;
function fit(r){var cw=el.clientWidth,ch=el.clientHeight,k=Math.min(cw*.96/r[2],ch*.96/r[3],3.2);return{s:k,tx:cw/2-(r[0]+r[2]/2)*k,ty:ch/2-(r[1]+r[3]/2)*k}}
function ap(){st.style.transform='translate('+tx+'px,'+ty+'px) scale('+s+')'}
function go(k,anim){key=k;var c=fit(P[k]);if(anim)st.classList.add('anim');else st.classList.remove('anim');s=c.s;tx=c.tx;ty=c.ty;ap();
[].forEach.call(bar.querySelectorAll('.pb'),function(b){b.classList.toggle('on',b.dataset.k===k)});if(anim)setTimeout(function(){st.classList.remove('anim')},1100)}
function f(){return el.getBoundingClientRect().width/el.clientWidth}
function zoom(m,cx,cy){st.classList.remove('anim');var n=Math.min(6,Math.max(.15,s*m));m=n/s;tx=cx-(cx-tx)*m;ty=cy-(cy-ty)*m;s=n;ap()}
el.addEventListener('wheel',function(e){e.preventDefault();var r=el.getBoundingClientRect(),q=f();zoom(Math.exp(-e.deltaY*(e.ctrlKey?.01:.0015)),(e.clientX-r.left)/q,(e.clientY-r.top)/q)},{passive:false});
var pt={};
el.addEventListener('pointerdown',function(e){st.classList.remove('anim');el.setPointerCapture(e.pointerId);pt[e.pointerId]=[e.clientX,e.clientY];el.classList.add('drag')});
el.addEventListener('pointermove',function(e){var p=pt[e.pointerId];if(!p)return;var q=f(),ids=Object.keys(pt);
if(ids.length===2){var o=pt[ids.filter(function(i){return +i!==e.pointerId})[0]],d0=Math.hypot(p[0]-o[0],p[1]-o[1]),d1=Math.hypot(e.clientX-o[0],e.clientY-o[1]),r=el.getBoundingClientRect();if(d0)zoom(d1/d0,((e.clientX+o[0])/2-r.left)/q,((e.clientY+o[1])/2-r.top)/q)}
else{tx+=(e.clientX-p[0])/q;ty+=(e.clientY-p[1])/q;ap()}pt[e.pointerId]=[e.clientX,e.clientY]});
function up(e){delete pt[e.pointerId];if(!Object.keys(pt).length)el.classList.remove('drag')}
el.addEventListener('pointerup',up);el.addEventListener('pointercancel',up);
el.addEventListener('dblclick',function(){go(key,true)});
bar.addEventListener('click',function(e){var b=e.target.closest('button');if(!b)return;if(b.dataset.k)go(b.dataset.k,true);else if(b.dataset.z==='r')go(key,true);
else zoom(b.dataset.z==='i'?1.25:.8,el.clientWidth/2,el.clientHeight/2)});
this.enter=function(){go('overview',false);var t=el.dataset.start;if(t!=='overview')setTimeout(function(){go(t,true)},60)};
go(key,false)}
[].forEach.call(deck.querySelectorAll('.cam'),function(el){el._c=new Cam(el)});
function fitDeck(){var k=Math.min(innerWidth/1280,(innerHeight-56)/720);deck.style.transform='translate('+((innerWidth-1280*k)/2)+'px,'+((innerHeight-56-720*k)/2+0)+'px) scale('+k+')'}
function show(i){if(i<0||i>=sl.length)return;cur=i;sl.forEach(function(s,j){s.classList.toggle('on',j===i)});
var cm=sl[i].querySelector('.cam');if(cm&&cm._c)cm._c.enter();document.getElementById('ct').textContent=(i+1)+' / '+sl.length;
document.getElementById('pb').style.width=((i+1)/sl.length*100)+'%';
document.getElementById('pv').disabled=i===0;document.getElementById('nx').disabled=i===sl.length-1;
try{history.replaceState(null,'','#'+(i+1))}catch(e){}}
sl.forEach(function(s,i){var p=s.querySelector('.pg');if(p)p.textContent=(i+1)+' / '+sl.length});
document.getElementById('nx').onclick=function(){show(cur+1)};
document.getElementById('pv').onclick=function(){show(cur-1)};
addEventListener('keydown',function(e){var k=e.key;
if(k==='ArrowRight'||k==='PageDown'||k===' '){e.preventDefault();show(cur+1)}
else if(k==='ArrowLeft'||k==='PageUp'){e.preventDefault();show(cur-1)}
else if(k==='Home')show(0);else if(k==='End')show(sl.length-1)});
addEventListener('resize',fitDeck);fitDeck();
var h=parseInt(location.hash.slice(1),10);show(h>=1&&h<=sl.length?h-1:0);
})();
