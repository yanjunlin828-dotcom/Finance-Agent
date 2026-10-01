/** All source text goes through textContent, including report Markdown. */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'value') node.value = value;
    else if (key === 'checked' || key === 'disabled' || key === 'open') node[key] = Boolean(value);
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function icon(type = 'arrow') {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.6'); svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true'); svg.classList.add('button-icon');
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', type === 'close' ? 'M6 6l12 12M18 6L6 18' : type === 'brand' ? 'M4 19V5h6v14M10 9h5v10M15 13h5v6M4 19h16' : 'M5 12h14M13 6l6 6-6 6');
  svg.append(path); return svg;
}

export function notice(text, tone = '') { return el('div', {class: `notice ${tone}`}, el('p', {}, text)); }
export function button(text, action, className = 'text-button') { return el('button', {type:'button', class:className, onclick:action}, text); }
export function empty(title, message) { return el('section', {class:'empty-state'}, el('h2',{},title), el('p',{},message)); }
