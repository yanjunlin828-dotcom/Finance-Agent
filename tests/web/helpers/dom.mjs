// Source-level DOM model. No browser, layout engine, or visual acceptance.
const dataKey=name=>name.slice(5).replace(/-([a-z])/g,(_,x)=>x.toUpperCase());
export class TreeNode {
  constructor(tag,text=''){
    this.tag=tag;this.childNodes=[];this.attrs={};this.parent=null;this.className='';this.text=text;this.dataset={};this.style={};this.listeners=new Map();
    this.scrollTop=0;this.scrollLeft=0;this.scrollHeight=40;this.scrollWidth=100;this.clientHeight=40;this.clientWidth=100;this.clientTop=0;
  }
  get children(){return this.childNodes.filter(node=>node.tag!=='#text');}
  get parentElement(){return this.parent;}
  get isConnected(){return this.tag==='#document'||Boolean(this.parent?.isConnected);}
  get textContent(){return this.text+this.childNodes.map(node=>node.textContent).join('');}
  set textContent(value){this.replaceChildren();this.text=String(value);}
  get previousElementSibling(){const siblings=this.parent?.children||[];return siblings[siblings.indexOf(this)-1];}
  get nextElementSibling(){const siblings=this.parent?.children||[];return siblings[siblings.indexOf(this)+1];}
  get classList(){return {
    add:value=>{if(!this.classList.contains(value))this.className=(this.className+' '+value).trim();},
    remove:value=>{this.className=this.className.split(' ').filter(x=>x!==value).join(' ');},
    contains:value=>this.className.split(' ').includes(value),
    toggle:(value,force)=>{const enabled=force??!this.classList.contains(value);this.classList[enabled?'add':'remove'](value);return enabled;}
  };}
  setAttribute(key,value){this.attrs[key]=String(value);if(key==='id')this.id=String(value);if(key==='class')this.className=String(value);if(key.startsWith('data-'))this.dataset[dataKey(key)]=String(value);if(key==='hidden')this.hidden=true;}
  getAttribute(key){if(key==='id')return this.id??null;if(key==='class')return this.className;if(key.startsWith('data-'))return this.dataset[dataKey(key)]??null;return this.attrs[key]??null;}
  hasAttribute(key){return this.getAttribute(key)!==null;}
  removeAttribute(key){delete this.attrs[key];if(key==='id')delete this.id;if(key.startsWith('data-'))delete this.dataset[dataKey(key)];if(key==='hidden')this.hidden=false;}
  addEventListener(type,handler){if(!this.listeners.has(type))this.listeners.set(type,new Set());this.listeners.get(type).add(handler);}
  removeEventListener(type,handler){this.listeners.get(type)?.delete(handler);}
  dispatchEvent(event){event.target??=this;event.preventDefault??=()=>{event.defaultPrevented=true;};for(const fn of this.listeners.get(event.type)||[])fn(event);return !event.defaultPrevented;}
  append(...nodes){for(const value of nodes){const node=value instanceof TreeNode?value:new TreeNode('#text',String(value));node.remove();node.parent=this;this.childNodes.push(node);}}
  prepend(node){this.append(node);this.childNodes.unshift(this.childNodes.pop());}
  remove(){if(this.parent)this.parent.childNodes.splice(this.parent.childNodes.indexOf(this),1);this.parent=null;}
  replaceChildren(...nodes){for(const node of this.childNodes)node.parent=null;this.childNodes=[];this.text='';this.append(...nodes);}
  matches(selectors){return selectors.split(',').some(raw=>{
    const selector=raw.trim(),parts=selector.split(/\s+/);
    if(parts.length>1){const last=parts.pop();return this.matches(last)&&Boolean(this.parent?.closest(parts.join(' ')));}
    if(selector==='*')return true;
    if(selector.startsWith('.'))return this.classList.contains(selector.slice(1));
    if(selector.startsWith('#'))return this.id===selector.slice(1);
    const match=/^([a-z][a-z0-9]*)?(?:\[([\w-]+)(?:="([^"]*)")?\])?$/.exec(selector);
    return Boolean(match&&(!match[1]||match[1]===this.tag)&&(!match[2]||(match[3]===undefined?this.hasAttribute(match[2]):this.getAttribute(match[2])===match[3])));
  });}
  closest(selector){return this.matches(selector)?this:this.parent?.closest(selector)||null;}
  querySelectorAll(selector){return this.children.flatMap(node=>[...(node.matches(selector)?[node]:[]),...node.querySelectorAll(selector)]);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  contains(node){return node===this||this.children.some(child=>child.contains(node));}
  getBoundingClientRect(){return {left:0,top:0,width:100,height:40,right:100,bottom:40};}
  focus(){globalThis.document.activeElement=this;}
  setCustomValidity(value){this.validityMessage=value;}
  reportValidity(){return !this.validityMessage;}
  scrollTo({top=0,left=0}){this.scrollTop=top;this.scrollLeft=left;}
  showModal(){this.open=true;}
  close(){this.open=false;this.dispatchEvent({type:'close'});}
}
export function createDocument(){
  const doc=new TreeNode('#document');
  Object.assign(doc,{createElement:tag=>new TreeNode(tag),createTextNode:text=>new TreeNode('#text',text),createElementNS:(_,tag)=>new TreeNode(tag),getElementById:id=>doc.querySelector('#'+id),hidden:false});
  return doc;
}
