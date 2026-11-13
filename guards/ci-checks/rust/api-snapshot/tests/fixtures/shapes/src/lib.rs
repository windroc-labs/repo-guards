use std::fmt::Debug;

pub fn generic<'a, T: AsRef<str> + ?Sized, const N: usize>(
    x: &'a T,
    f: fn(u8) -> u8,
    d: Box<dyn Fn(u8) -> bool + Send + 'a>,
    i: impl Iterator<Item = u8>,
) -> [u8; N]
where
    T: Sync,
{
    let _ = (x, f, d, i);
    [0; N]
}

pub const unsafe extern "C" fn raw(p: *const u8, q: *mut u8) -> (u8, u16) {
    let _ = (p, q);
    (0, 0)
}

pub async fn later(s: &mut [u8]) {
    let _ = s;
}

pub struct Unit;

pub struct Tuple(pub u8, u16);

pub struct Wrapper<T: Clone = u8> {
    pub inner: Vec<T>,
}

pub enum Shape<T> {
    Point,
    Circle(T),
    Rect { w: T, h: T },
}

#[repr(u8)]
pub enum Code {
    A = 1,
    B = 1 + 1,
}

pub union Bits {
    pub int: u32,
    pub float: f32,
}

pub static mut COUNTER: i64 = 0;

pub type Pair<'a> = (&'a str, Option<u8>);

pub unsafe trait Visitor<T>: Debug
where
    T: Clone,
{
    type Out: Default;
    const LIMIT: usize = 4;

    fn visit(&mut self, value: T) -> Self::Out;

    fn done(&self) -> bool {
        true
    }
}

impl<T: Clone> Wrapper<T> {
    pub fn first(&self) -> Option<&T> {
        self.inner.first()
    }
}

impl Iterator for Unit {
    type Item = u8;

    fn next(&mut self) -> Option<<Self as Iterator>::Item> {
        None
    }
}

#[macro_export]
macro_rules! square {
    ($x:expr) => {
        $x * $x
    };
}

pub struct NotSend(pub *const u8);
