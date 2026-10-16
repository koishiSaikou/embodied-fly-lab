import assert from 'node:assert/strict';
import {toMap,fromMap} from './web/arena_tools.js';
for(const [width,height] of [[800,800],[340,340],[1330,1330]]){
  const rect={left:37,top:61,width,height};
  for(const [x,y]of [[-25,-25],[0,0],[25,25],[12,-13],[-9.5,8.2]]){
    const [u,v]=toMap(x,y),point=fromMap(rect.left+u/600*width,rect.top+v/600*height,rect);
    assert.ok(Math.abs(point.x-x)<1e-10);assert.ok(Math.abs(point.y-y)<1e-10);
  }
}
console.log('Map rendering / click coordinates match across wide and narrow layouts.');
