// GBIF identifies cached media by the MD5 of the original identifier URL.
// This is a cache key, not a security primitive. https://techdocs.gbif.org/en/openapi/images
export function mediaIdentifierHash(value) {
  const bytes=new TextEncoder().encode(value), length=Math.ceil((bytes.length+9)/64)*64;
  const block=new Uint8Array(length);block.set(bytes);block[bytes.length]=0x80;
  const view=new DataView(block.buffer);view.setUint32(length-8,(bytes.length*8)>>>0,true);view.setUint32(length-4,Math.floor(bytes.length/536870912),true);
  const state=[0x67452301,0xefcdab89,0x98badcfe,0x10325476];
  const shifts=[[7,12,17,22],[5,9,14,20],[4,11,16,23],[6,10,15,21]];
  for(let offset=0;offset<length;offset+=64) {
    let [a,b,c,d]=state;
    for(let i=0;i<64;i++) {
      let f,g;
      if(i<16){f=(b&c)|(~b&d);g=i;}else if(i<32){f=(d&b)|(~d&c);g=(5*i+1)%16;}
      else if(i<48){f=b^c^d;g=(3*i+5)%16;}else {f=c^(b|~d);g=(7*i)%16;}
      const k=Math.floor(Math.abs(Math.sin(i+1))*4294967296), shift=shifts[Math.floor(i/16)][i%4];
      const sum=(a+f+k+view.getUint32(offset+g*4,true))>>>0;
      const next=(b+((sum<<shift)|(sum>>>(32-shift))))>>>0;
      a=d;d=c;c=b;b=next;
    }
    [a,b,c,d].forEach((word,i)=>state[i]=(state[i]+word)>>>0);
  }
  return state.flatMap(word=>[0,8,16,24].map(shift=>((word>>>shift)&255).toString(16).padStart(2,"0"))).join("");
}
export function occurrenceImageUrl(occurrenceId, identifier) {
  if(!/^[1-9]\d*$/.test(String(occurrenceId)))return null;
  return `https://api.gbif.org/v1/image/cache/640x/occurrence/${occurrenceId}/media/${mediaIdentifierHash(identifier)}`;
}
