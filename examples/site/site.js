// The site's OWN behaviour: identical in a normal browser and under promokit.
document.querySelectorAll('.q').forEach(q => q.addEventListener('click', () => q.classList.toggle('open')));
// Reveal-on-scroll, the way real sites do it.
const io = new IntersectionObserver(entries => {
  entries.forEach(e => { if (e.isIntersecting) e.target.classList.add('in'); });
}, { threshold: 0.25 });
document.querySelectorAll('.reveal').forEach(el => io.observe(el));
